"""Minimal offline Weave compatibility shim for local RAT runs.

The RAT harness decorates models and scorers with ``weave.op`` and subclasses
``weave.Model``.  The offline runner does not use Weave tracking, so this shim
keeps those imports working without requiring W&B credentials or the weave
package.
"""

from __future__ import annotations


def op(func=None, **_kwargs):
    def decorator(inner):
        return inner

    if func is None:
        return decorator
    return func


class Model:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Evaluation:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    async def evaluate(self, *_args, **_kwargs):
        return {}


class Dataset:
    def __init__(self, rows=None, **kwargs):
        self.rows = rows or []
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def from_hf(cls, dataset):
        return cls(rows=list(dataset) if dataset is not None else [])


def init(*_args, **_kwargs):
    return None


def publish(obj, *_args, **_kwargs):
    return obj


class _Ref:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value


def ref(_uri):
    return _Ref()
