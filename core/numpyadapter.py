# -*- coding: utf-8 -*-
# @Time:  22:34
# @Author: tk
# @File：numpyadapter

import copy
import typing
import warnings
from enum import Enum

import numpy as np
from fastdatasets.utils.py_features import Final
from fastdatasets.record import writer as record_writer, RECORD, load_dataset as record_loader
from fastdatasets.leveldb import writer as leveldb_writer, LEVELDB, load_dataset as leveldb_loader
from fastdatasets.lmdb import writer as lmdb_writer, LMDB, load_dataset as lmdb_loader
from fastdatasets.memory import writer as memory_writer, MEMORY, load_dataset as memory_loader
from fastdatasets.arrow import writer as arrow_writer,load_dataset as arrow_loader
from fastdatasets.parquet import writer as parquet_writer,load_dataset as parquet_loader
from .parallel import ParallelNode, parallel_apply


__all__ = [
    'E_file_backend',
    'NumpyWriterAdapter',
    'NumpyReaderAdapter',
    'ParallelNode',
    'parallel_apply',
    'ParallelNumpyWriter'
]


class E_file_backend(Enum):
    record = 0
    leveldb = 1
    lmdb = 2
    memory = 3
    memory_raw = 4
    arrow_stream = 5
    arrow_file = 6
    parquet = 7


    @staticmethod
    def from_string(b: str):
        b = b.lower()
        if b == 'record':
            return E_file_backend.record
        elif b == 'leveldb':
            return E_file_backend.leveldb
        elif b == 'lmdb':
            return E_file_backend.lmdb
        elif b == 'memory':
            return E_file_backend.memory
        elif b == 'memory_raw':
            return E_file_backend.memory_raw
        elif b == 'arrow_stream':
            return E_file_backend.arrow_stream
        elif b == 'arrow_file':
            return E_file_backend.arrow_file
        elif b == 'parquet':
            return E_file_backend.parquet
        return None

    def to_string(self, b):
        if b == E_file_backend.record:
            return 'record'
        if b == E_file_backend.leveldb:
            return 'leveldb'
        if b == E_file_backend.lmdb:
            return 'lmdb'
        if b == E_file_backend.memory:
            return 'memory'
        if b == E_file_backend.memory_raw:
            return 'memory_raw'
        if b == E_file_backend.arrow_stream:
            return 'arrow_stream'
        if b == E_file_backend.arrow_file:
            return 'arrow_file'
        if b == E_file_backend.parquet:
            return 'parquet'
        return None


class NumpyWriterAdapter:
    def __init__(self, filename: typing.Union[str, typing.List],
                 backend: typing.Union[E_file_backend, str],
                 options: typing.Union[
                     RECORD.TFRecordOptions,
                     LEVELDB.LeveldbOptions,
                     LMDB.LmdbOptions,
                     MEMORY.MemoryOptions,
                     typing.Dict,
                     typing.AnyStr
                 ] = None,
                 parquet_options: typing.Optional = None,
                 schema : typing.Optional[typing.Dict] = None,
                 leveldb_write_buffer_size=1024 * 1024 * 512,
                 leveldb_max_file_size=10 * 1024 * 1024 * 1024,
                 lmdb_map_size=1024 * 1024 * 1024 * 150,
                 batch_size=None):

        self.filename = filename
        self.schema = schema
        if isinstance(backend, E_file_backend):
            self._backend_type = E_file_backend.to_string(backend)
            self._backend = backend
        else:
            self._backend_type = backend
            self._backend = E_file_backend.from_string(backend)
        self._buffer_batch_size = 2000
        self._kv_flag = True
        self._is_table = False
        if self._backend == E_file_backend.record:
            self._kv_flag = False
            self._buffer_batch_size = 2000
            if options is None:
                options = RECORD.TFRecordOptions(compression_type='GZIP')
            self._f_writer = record_writer.NumpyWriter(filename, options=options)

        elif self._backend == E_file_backend.leveldb:
            self._buffer_batch_size = 100000
            if options is None:
                options = LEVELDB.LeveldbOptions(create_if_missing=True,
                                                 error_if_exists=False,
                                                 write_buffer_size=leveldb_write_buffer_size,
                                                 max_file_size=leveldb_max_file_size)
            self._f_writer = leveldb_writer.NumpyWriter(filename, options=options)
        elif self._backend == E_file_backend.lmdb:
            self._buffer_batch_size = 100000
            if options is None:
                options = LMDB.LmdbOptions(env_open_flag=0,
                                           env_open_mode=0o664,  # 8进制表示
                                           txn_flag=0,
                                           dbi_flag=0,
                                           put_flag=0)
            self._f_writer = lmdb_writer.NumpyWriter(filename, options=options,
                                                     map_size=lmdb_map_size)
        elif self._backend == E_file_backend.memory:
            self._kv_flag = False
            self._buffer_batch_size = 100000
            if options is None:
                options = MEMORY.MemoryOptions()
            self._f_writer = memory_writer.NumpyWriter(filename, options=options)
        elif self._backend == E_file_backend.memory_raw:
            self._kv_flag = False
            self._buffer_batch_size = 100000
            if options is None:
                options = MEMORY.MemoryOptions()
            self._f_writer = memory_writer.WriterObject(filename, options=options)

        # table
        elif self._backend == E_file_backend.arrow_stream:
            self._kv_flag = False
            self._buffer_batch_size = 1024
            self._is_table = True
            self._f_writer = arrow_writer.PythonWriter(filename,with_stream=True,schema=schema, options=options)

        elif self._backend == E_file_backend.arrow_file:
            self._kv_flag = False
            self._buffer_batch_size = 1024
            self._is_table = True
            self._f_writer = arrow_writer.PythonWriter(filename, with_stream=False, schema=schema, options=options)
        elif self._backend == E_file_backend.parquet:
            self._kv_flag = False
            self._buffer_batch_size = 1024
            self._is_table = True
            self._f_writer = parquet_writer.PythonWriter(filename,
                                                         schema=schema,
                                                         arrow_options=options,
                                                         parquet_options = parquet_options)

        else:
            raise ValueError(
                'NumpyWriterAdapter does not support backend={} , not in record,leveldb,lmdb,memory,meory_raw'.format(
                    backend))

        if batch_size is not None:
            self._buffer_batch_size = batch_size
        assert self._buffer_batch_size > 0
    def __del__(self):
        self.close()

    def close(self):
        if self._f_writer is not None:
            self._f_writer.close()
            self._f_writer = None

    @property
    def writer(self):
        return self._f_writer

    @property
    def is_table(self):
        return self._is_table
    @property
    def is_kv_writer(self):
        return self._kv_flag

    @property
    def backend(self):
        return self._backend

    @property
    def backend_type(self):
        return self._backend_type

    @property
    def buffer_batch_size(self):
        return self._buffer_batch_size

    @buffer_batch_size.setter
    def buffer_batch_size(self,batch_size):
        self._buffer_batch_size = batch_size

class NumpyReaderAdapter:
    @staticmethod
    def load(input_files: typing.Union[typing.List[str], str, typing.List[typing.Any]],
             backend: typing.Union[E_file_backend, str],
             options: typing.Union[
                 RECORD.TFRecordOptions,
                 LEVELDB.LeveldbOptions,
                 LMDB.LmdbOptions,
                 MEMORY.MemoryOptions,
                 typing.Dict,
                 typing.AnyStr
             ] = None,
             col_names: typing.Optional[typing.Dict] = None,
             data_key_prefix_list=('input',),
             num_key='total_num',
             cycle_length=1,
             block_length=1,
             with_record_iterable_dataset=True,
             with_parse_from_numpy=True,
             with_share_memory=True):
        '''
            input_files: 文件列表
            backend: 存储引擎类型
            options: 存储引擎选项
            data_key_prefix_list: 键值数据库 键值前缀
            num_key: 键值数据库，记录数据总数建
            with_record_iterable_dataset 打开iterable_dataset
            with_parse_from_numpy 解析numpy数据
        '''

        parse_flag = True
        data_backend = backend if isinstance(backend, E_file_backend) else E_file_backend.from_string(backend)
        if data_backend == E_file_backend.record:
            if options is None:
                options = RECORD.TFRecordOptions(compression_type='GZIP')
            if with_record_iterable_dataset:
                dataset = record_loader.IterableDataset(input_files,
                                                        cycle_length=cycle_length,
                                                        block_length=block_length,
                                                        options=options,
                                                        with_share_memory=with_share_memory)
            else:
                dataset = record_loader.RandomDataset(input_files,
                                                      options=options,
                                                      with_share_memory=with_share_memory)

        elif data_backend == E_file_backend.leveldb:
            if options is None:
                options = LEVELDB.LeveldbOptions(create_if_missing=True, error_if_exists=False)
            dataset = leveldb_loader.RandomDataset(input_files,
                                                   data_key_prefix_list=data_key_prefix_list,
                                                   num_key=num_key,
                                                   options=options)
        elif data_backend == E_file_backend.lmdb:
            if options is None:
                options = LMDB.LmdbOptions(env_open_flag=LMDB.LmdbFlag.MDB_RDONLY,
                                           env_open_mode=0o664,  # 8进制表示
                                           txn_flag=LMDB.LmdbFlag.MDB_RDONLY,
                                           dbi_flag=0,
                                           put_flag=0)
            dataset = lmdb_loader.RandomDataset(input_files,
                                                data_key_prefix_list=data_key_prefix_list,
                                                num_key=num_key,
                                                options=options)
        elif data_backend == E_file_backend.memory:
            if options is None:
                options = MEMORY.MemoryOptions()
            dataset = memory_loader.RandomDataset(input_files, options=options)
        elif data_backend == E_file_backend.memory_raw:
            parse_flag = False
            if options is None:
                options = MEMORY.MemoryOptions()
            dataset = memory_loader.RandomDataset(input_files, options=options)
        elif data_backend == E_file_backend.arrow_stream:
            parse_flag = False
            if with_record_iterable_dataset:
                dataset = arrow_loader.IterableDataset(input_files,
                                                       cycle_length=cycle_length,
                                                       block_length=block_length,
                                                       options=options,
                                                       col_names=col_names,
                                                       with_share_memory=False)
            else:
                dataset = arrow_loader.RandomDataset(input_files,
                                                     options=options,
                                                     col_names=col_names,
                                                     with_share_memory=False)
        elif data_backend == E_file_backend.arrow_file:
            parse_flag = False
            if with_record_iterable_dataset:
                dataset = arrow_loader.IterableDataset(input_files,
                                                       cycle_length=cycle_length,
                                                       block_length=block_length,
                                                       options=options,
                                                       col_names=col_names,
                                                       with_share_memory=True)
            else:
                dataset = arrow_loader.RandomDataset(input_files,
                                                     options=options,
                                                     col_names=col_names,
                                                     with_share_memory=True)
        elif data_backend == E_file_backend.parquet:
            parse_flag = False
            if with_record_iterable_dataset:
                dataset = parquet_loader.IterableDataset(input_files,
                                                         cycle_length=cycle_length,
                                                         block_length=block_length,
                                                         options=options,
                                                         with_share_memory=True)
            else:
                dataset = parquet_loader.RandomDataset(input_files,
                                                       options=options,
                                                       col_names=col_names,
                                                       with_share_memory=True)
        else:
            dataset = None
            warnings.warn('no support databackend')
        if with_parse_from_numpy and parse_flag:
            dataset = dataset.parse_from_numpy_writer()
        return dataset


class ParallelNumpyWriter(ParallelNode, metaclass=Final):
    def __init__(self, *args, **kwargs):
        ParallelNode.__init__(self, *args, **kwargs)
        self.batch_keys = []
        self.batch_values = []
        self.total_num = 0
        self.numpy_writer = None

    def open(self, outfile: typing.Union[str, typing.List],
             backend: typing.Union[E_file_backend, str],
             options: typing.Union[
                 RECORD.TFRecordOptions,
                 LEVELDB.LeveldbOptions,
                 LMDB.LmdbOptions,
                 MEMORY.MemoryOptions,
                 typing.Dict,
                 typing.AnyStr
             ] = None,
             parquet_options: typing.Optional = None,
             schema: typing.Optional[typing.Dict] = None,
             leveldb_write_buffer_size=1024 * 1024 * 512,
             leveldb_max_file_size=10 * 1024 * 1024 * 1024,
             lmdb_map_size=1024 * 1024 * 1024 * 150,
             batch_size=None):

        self.numpy_writer = NumpyWriterAdapter(outfile,
                                               backend = backend,
                                               options=options,
                                               parquet_options=parquet_options,
                                               schema=schema,
                                               leveldb_write_buffer_size = leveldb_write_buffer_size,
                                               leveldb_max_file_size=leveldb_max_file_size,
                                               lmdb_map_size = lmdb_map_size,
                                               batch_size=batch_size)
        self.backend = self.numpy_writer.backend
        self.backend_type = self.numpy_writer.backend_type
        self.is_kv_writer = self.numpy_writer.is_kv_writer
        self.is_table = self.numpy_writer.is_table
        self.schema = self.numpy_writer.schema
        self.write_batch_size = self.numpy_writer.buffer_batch_size

    def write(self,
              data: typing.Union[typing.Sequence,typing.Iterator],
              input_hook_fn: typing.Callable,
              fn_args: typing.Union[typing.Tuple, typing.Dict],
              write_batch_size=None):
        self.input_hook_fn = input_hook_fn
        self.fn_args = fn_args

        assert self.numpy_writer is not None
        assert self.input_hook_fn is not None

        if write_batch_size is None or write_batch_size <= 0:
            write_batch_size = self.numpy_writer.buffer_batch_size

            if isinstance(data,typing.Sequence):
                if write_batch_size >= len(data):
                    write_batch_size = len(data) // 2

        if write_batch_size <= 0:
            write_batch_size = 1

        self.write_batch_size = write_batch_size
        parallel_apply(data, self)

    def flush(self):
        if self.is_table:
            values  = {k: [] for k in self.schema.keys()}
            for d in self.batch_values:
                for k,v in values.items():
                    if isinstance(d[k],np.ndarray):
                        data = d[k].tolist()
                    else:
                        data = d[k]
                    v.append(data)
            self.numpy_writer.writer.write_batch(list(values.keys()),list(values.values()))

        elif not self.is_kv_writer:
            if self.backend == E_file_backend.memory_raw:
                self.numpy_writer.writer.write_batch([d for d in self.batch_values])
            else:
                self.numpy_writer.writer.write_batch(self.batch_values)
        else:
            self.numpy_writer.writer.put_batch(self.batch_keys, self.batch_values)
        self.batch_keys.clear()
        self.batch_values.clear()

    # 继承
    def on_input_process(self, x):
        return self.input_hook_fn(x, self.fn_args)

    # 继承
    def on_output_process(self, x):
        #忽略None数据
        if x is None:
            return

        if not isinstance(x, (list, tuple)):
            x = [x]
        for one in x:
            self.batch_keys.append('input{}'.format(self.total_num))
            self.batch_values.append(one)
            self.total_num += 1

        if len(self.batch_values) > 0 and len(self.batch_values) % self.write_batch_size == 0:
            self.flush()

    # 继承
    def on_output_cleanup(self):
        if self.numpy_writer is not None:
            if len(self.batch_values) > 0:
                self.flush()
            if self.is_kv_writer:
                self.numpy_writer.writer.file_writer.put('total_num', str(self.total_num))
            self.numpy_writer.close()
            self.numpy_writer = None

