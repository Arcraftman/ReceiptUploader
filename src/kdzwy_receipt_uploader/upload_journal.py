"""Durable upload intent: never repeat a voucher save with an uncertain outcome."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal, cast

from .models import Receipt, ReceiptError

UploadPhase = Literal['prepared', 'saving', 'saved', 'binding', 'verified']


class UploadRecoveryRequired(ReceiptError):
    """Remote outcome or local journal requires reconciliation before retry."""


class UploadInProgress(UploadRecoveryRequired):
    """Another process owns the receipt; leave its files and ledger untouched."""


@dataclass
class UploadState:
    version: int = 1
    fingerprint: str = ''
    phase: UploadPhase = 'prepared'
    file_ids: list[str] = field(default_factory=list)
    voucher_id: str = ''
    voucher_no: str = ''
    result: dict[str, Any] = field(default_factory=dict)
    tenant: str = ''
    receipt_id: str = ''

    @classmethod
    def parse(cls, value: object) -> UploadState:
        if not isinstance(value, dict) or value.get('version') != 1:
            raise UploadRecoveryRequired('提交日志版本或结构无效；禁止重新保存凭证')
        phase = value.get('phase')
        if phase not in ('prepared', 'saving', 'saved', 'binding', 'verified'):
            raise UploadRecoveryRequired('提交日志阶段无效；禁止重新保存凭证')
        for key in ('fingerprint', 'voucher_id', 'voucher_no', 'tenant', 'receipt_id'):
            if not isinstance(value.get(key), str):
                raise UploadRecoveryRequired('提交日志身份字段无效')
        ids = value.get('file_ids')
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
            raise UploadRecoveryRequired('提交日志附件字段无效')
        result = value.get('result')
        if not isinstance(result, dict):
            raise UploadRecoveryRequired('提交日志结果字段无效')
        if phase in ('prepared', 'saving') and value['voucher_id']:
            raise UploadRecoveryRequired('提交日志阶段与凭证 ID 不一致')
        if phase in ('saved', 'binding', 'verified') and not value['voucher_id']:
            raise UploadRecoveryRequired('提交日志缺少已保存凭证 ID')
        if phase == 'verified' and (result.get('status') != 'submitted_and_verified' or result.get('voucherId') != value['voucher_id']):
            raise UploadRecoveryRequired('提交日志完成结果不一致')
        return cls(1, value['fingerprint'], cast(UploadPhase, phase), ids,
                   value['voucher_id'], value['voucher_no'], result, value['tenant'], value['receipt_id'])


def receipt_fingerprint(receipt: Receipt) -> str:
    attachments = []
    for item in receipt.attachment_files:
        digest = hashlib.sha256(item.path.read_bytes()).hexdigest()
        attachments.append({'name': item.path.name, 'sha256': digest})
    body = {'receiptId': receipt.receipt_id, 'source': receipt.source,
            'voucher': receipt.voucher, 'attachments': attachments}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Kernel lock released on process death; never remove the lock inode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UploadInProgress('该凭证正在由另一个进程处理') from exc
        try:
            yield
        finally:
            if sys.platform == 'win32':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class UploadJournal:
    def __init__(self, directory: Path, tenant: str, receipt_id: str) -> None:
        key = hashlib.sha256(json.dumps([tenant, receipt_id]).encode()).hexdigest()
        self.tenant = tenant
        self.receipt_id = receipt_id
        self.path = directory / (key + '.json')
        self.lock_path = directory / (key + '.lock')
        self.state = UploadState()

    def load(self, receipt: Receipt) -> UploadState:
        fingerprint = receipt_fingerprint(receipt)
        if self.path.exists():
            try:
                self.state = UploadState.parse(json.loads(self.path.read_text(encoding='utf-8')))
            except (OSError, ValueError) as exc:
                raise UploadRecoveryRequired('提交日志不可读取；禁止重新保存凭证') from exc
            if self.state.tenant != self.tenant or self.state.receipt_id != receipt.receipt_id:
                raise UploadRecoveryRequired('提交日志账套或凭证身份不一致')
            if self.state.fingerprint != fingerprint:
                raise UploadRecoveryRequired('凭证或附件内容已变化；先核对原提交记录，禁止覆盖重试')
        else:
            self.state = UploadState(fingerprint=fingerprint, tenant=self.tenant, receipt_id=receipt.receipt_id)
            self.write()
        if len(self.state.file_ids) > len(receipt.attachment_files):
            raise UploadRecoveryRequired('提交日志附件数量超出原凭证')
        if self.state.phase == 'saving':
            raise UploadRecoveryRequired('保存结果不明；必须先核对远端凭证，禁止自动重新保存')
        return self.state

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.' + self.path.name, suffix='.tmp', dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(asdict(self.state), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if sys.platform != 'win32':
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
