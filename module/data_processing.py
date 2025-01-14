from dataclasses import dataclass
from typing import Dict, List, Union, Any

import torch
import torchaudio
from transformers import Wav2Vec2Processor


def encode_dataset(batch, processor, is_phonemize, backend=None, separator=None):
    if not isinstance(batch["labels"], list):
        if is_phonemize:
            with processor.as_target_processor():
                batch["labels"] = processor(backend.phonemize([batch["labels"]], separator=separator)[0]).input_ids
        else:
            try:
                with processor.as_target_processor():
                    line = bytes(batch["labels"], 'utf-8').decode('utf-8', 'ignore')
                    batch["labels"] = processor(line).input_ids
            except Exception as e:
                line = bytes(batch["labels"], 'utf-8').decode('utf-8', 'ignore')
                batch["labels"] = processor.tokenizer(line).input_ids
    return batch


def prepare_dataset_hf(batch, processor):
    audio = batch["audio"]
    batch["input_features"] = processor(audio["array"], sampling_rate=audio["sampling_rate"]).input_values[0]
    batch["lengths"] = len(batch["input_features"])
    if 'sentence' in batch:
        batch["labels"] = batch["sentence"]
    else:
        batch["labels"] = batch["text"]
    return batch


def prepare_dataset_custom(batch):
    path = batch["path"]
    speech, sampling_rate = torchaudio.load(path)
    if sampling_rate != '16_000' or sampling_rate != '16000':
        resampler = torchaudio.transforms.Resample(orig_freq=sampling_rate, new_freq=16_000)
        batch["input_features"] = resampler.forward(speech.squeeze(0)).numpy()
    else:
        batch["input_features"] = speech.squeeze(0).numpy()
    batch["lengths"] = len(batch["input_features"])
    if 'sentence' in batch:
        batch["labels"] = batch["sentence"]
    else:
        batch["labels"] = batch["text"]
    return batch


def prepare_dataset_whisper(batch, feature_extractor):
    # compute log-Mel input features from input audio array
    if 'input_values' in batch:
        batch["input_features"] = feature_extractor(batch["input_values"], sampling_rate=16000).input_features[0]
    else:
        batch["input_features"] = feature_extractor(batch["input_features"], sampling_rate=16000).input_features[0]

    # # encode target text to label ids
    # batch["labels"] = tokenizer(batch["sentence"]).input_ids
    return batch


@dataclass
class DataCollatorCTCWithPadding:
    processor: Wav2Vec2Processor
    padding: Union[bool, str] = True

    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        # split inputs and labels since they have to be of different lenghts and need
        # different padding methods
        input_features = [{"input_features": feature["input_features"]} for feature in features]
        label_features = [{"input_ids": feature["labels"]} for feature in features]

        batch = self.processor.pad(
            input_features,
            padding=self.padding,
            return_tensors="pt",
        )
        with self.processor.as_target_processor():
            labels_batch = self.processor.pad(
                label_features,
                padding=self.padding,
                return_tensors="pt",
            )

        # replace padding with -100 to ignore loss correctly
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        batch["labels"] = labels
        return batch


@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: Any

    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        # split inputs and labels since they have to be of different lengths and need different padding methods
        # first treat the audio inputs by simply returning torch tensors
        input_features = [{"input_features": feature["input_features"]} for feature in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        # get the tokenized label sequences
        label_features = [{"input_ids": feature["labels"]} for feature in features]
        # pad the labels to max length
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")

        # replace padding with -100 to ignore loss correctly
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        # if bos token is appended in previous tokenization step,
        # cut bos token here as it's append later anyways
        if (labels[:, 0] == self.processor.tokenizer.bos_token_id).all().cpu().item():
            labels = labels[:, 1:]

        batch["labels"] = labels

        return batch
