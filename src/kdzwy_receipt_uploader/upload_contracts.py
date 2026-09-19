"""Serialized upload result contract shared by workflow and recovery."""
from typing import Literal, TypedDict

class UploadResult(TypedDict):
    status: Literal["submitted_and_verified"]
    apiVersion: Literal["vip4-v1"]
    receiptId: str
    voucherId: str
    voucherNo: str
    voucherReadback: object
    auxiliaryReadback: list[dict[str, object]]
    attachmentStatus: Literal["not_requested", "uploaded_linked_and_verified"]
    attachmentFileIds: list[str]
    unresolvedInvoiceCodes: list[str]
    completedAt: str


class VerifiedAttachmentResult(UploadResult, total=False):
    voucherReadbackAfterAttachment: object
