# Copyright (c) OpenMMLab. All rights reserved.
import json
import os.path as osp
import warnings
from typing import Callable, List, Optional, Sequence, Union

from mmengine.dataset import BaseDataset

from mmocr.registry import DATASETS, TASK_UTILS


@DATASETS.register_module()
class RecogLMDBDataset(BaseDataset):
    r"""RecogLMDBDataset for text recognition.

    The annotation format should be in lmdb format. We support two lmdb
    formats, one is the lmdb file with only labels generated by txt2lmdb
    (deprecated), and another one is the lmdb file generated by recog2lmdb.

    The former format stores string in `filename text` format directly in lmdb,
    while the latter uses `image_key` as well as `label_key` for querying.

    Args:
        ann_file (str): Annotation file path. Defaults to ''.
        parse_cfg (dict, optional): Config of parser for parsing annotations.
            Use ``LineJsonParser`` when the annotation file is in jsonl format
            with keys of ``filename`` and ``text``. The keys in parse_cfg
            should be consistent with the keys in jsonl annotations. The first
            key in parse_cfg should be the key of the path in jsonl
            annotations. The second key in parse_cfg should be the key of the
            text in jsonl Use ``LineStrParser`` when the annotation file is in
            txt format. Defaults to
            ``dict(type='LineJsonParser', keys=['filename', 'text'])``.
        metainfo (dict, optional): Meta information for dataset, such as class
            information. Defaults to None.
        data_root (str): The root directory for ``data_prefix`` and
            ``ann_file``. Defaults to ''.
        data_prefix (dict): Prefix for training data. Defaults to
            ``dict(img_path='')``.
        filter_cfg (dict, optional): Config for filter data. Defaults to None.
        indices (int or Sequence[int], optional): Support using first few
            data in annotation file to facilitate training/testing on a smaller
            dataset. Defaults to None which means using all ``data_infos``.
        serialize_data (bool, optional): Whether to hold memory using
            serialized objects, when enabled, data loader workers can use
            shared RAM from master process instead of making a copy. Defaults
            to True.
        pipeline (list, optional): Processing pipeline. Defaults to [].
        test_mode (bool, optional): ``test_mode=True`` means in test phase.
            Defaults to False.
        lazy_init (bool, optional): Whether to load annotation during
            instantiation. In some cases, such as visualization, only the meta
            information of the dataset is needed, which is not necessary to
            load annotation file. ``RecogLMDBDataset`` can skip load
            annotations to save time by set ``lazy_init=False``.
            Defaults to False.
        max_refetch (int, optional): If ``RecogLMDBdataset.prepare_data`` get a
            None img. The maximum extra number of cycles to get a valid
            image. Defaults to 1000.
    """

    def __init__(self,
                 ann_file: str = '',
                 parser_cfg: Optional[dict] = dict(
                     type='LineJsonParser', keys=['filename', 'text']),
                 metainfo: Optional[dict] = None,
                 data_root: Optional[str] = '',
                 data_prefix: dict = dict(img_path=''),
                 filter_cfg: Optional[dict] = None,
                 indices: Optional[Union[int, Sequence[int]]] = None,
                 serialize_data: bool = True,
                 pipeline: List[Union[dict, Callable]] = [],
                 test_mode: bool = False,
                 lazy_init: bool = False,
                 max_refetch: int = 1000) -> None:
        if parser_cfg['type'] != 'LineJsonParser':
            raise ValueError('We only support using LineJsonParser '
                             'to parse lmdb file. Please use LineJsonParser '
                             'in the dataset config')
        self.parser = TASK_UTILS.build(parser_cfg)
        self.ann_file = ann_file
        self.deprecated_format = False
        env = self._get_env()
        with env.begin(write=False) as txn:
            try:
                self.total_number = int(
                    txn.get(b'num-samples').decode('utf-8'))
            except AttributeError:
                warnings.warn(
                    'DeprecationWarning: The lmdb dataset generated with '
                    'txt2lmdb will be deprecate, please use the latest '
                    'tools/data/utils/recog2lmdb to generate lmdb dataset. '
                    'See https://mmocr.readthedocs.io/en/latest/tools.html#'
                    'convert-text-recognition-dataset-to-lmdb-format for '
                    'details.', UserWarning)
                self.total_number = int(
                    txn.get(b'total_number').decode('utf-8'))
                self.deprecated_format = True
            # The lmdb file may contain only the label, or it may contain both
            # the label and the image, so we use image_key here for probing.
            image_key = f'image-{1:09d}'
            if txn.get(image_key.encode('utf-8')) is None:
                self.label_only = True
            else:
                self.label_only = False

        super().__init__(
            ann_file=ann_file,
            metainfo=metainfo,
            data_root=data_root,
            data_prefix=data_prefix,
            filter_cfg=filter_cfg,
            indices=indices,
            serialize_data=serialize_data,
            pipeline=pipeline,
            test_mode=test_mode,
            lazy_init=lazy_init,
            max_refetch=max_refetch)

    def load_data_list(self) -> List[dict]:
        """Load annotations from an annotation file named as ``self.ann_file``

        Returns:
            List[dict]: A list of annotation.
        """
        if not hasattr(self, 'env'):
            self.env = self._get_env()

        data_list = []
        with self.env.begin(write=False) as txn:
            for i in range(self.total_number):
                if self.deprecated_format:
                    line = txn.get(str(i).encode('utf-8')).decode('utf-8')
                    filename, text = line.strip('/n').split(' ')
                    line = json.dumps(
                        dict(filename=filename, text=text), ensure_ascii=False)
                else:
                    i = i + 1
                    label_key = f'label-{i:09d}'
                    if self.label_only:
                        line = txn.get(
                            label_key.encode('utf-8')).decode('utf-8')
                    else:
                        img_key = f'image-{i:09d}'
                        text = txn.get(
                            label_key.encode('utf-8')).decode('utf-8')
                        line = json.dumps(
                            dict(filename=img_key, text=text),
                            ensure_ascii=False)
                data_list.append(self.parse_data_info(line))
        return data_list

    def parse_data_info(self, raw_anno_info: str) -> Union[dict, List[dict]]:
        """Parse raw annotation to target format.

        Args:
            raw_anno_info (str): One raw data information loaded
                from ``ann_file``.

        Returns:
            (dict): Parsed annotation.
        """
        data_info = {}
        parsed_anno = self.parser(raw_anno_info)
        img_path = osp.join(self.data_prefix['img_path'],
                            parsed_anno[self.parser.keys[0]])

        data_info['img_path'] = img_path
        data_info['instances'] = [dict(text=parsed_anno[self.parser.keys[1]])]
        return data_info

    def _get_env(self):
        """Get lmdb environment from self.ann_file.

        Returns:
            Lmdb environment.
        """
        try:
            import lmdb
        except ImportError:
            raise ImportError(
                'Please install lmdb to enable RecogLMDBDataset.')
        return lmdb.open(
            self.ann_file,
            max_readers=1,
            readonly=True,
            lock=False,
            readahead=False,
            meminit=False,
        )