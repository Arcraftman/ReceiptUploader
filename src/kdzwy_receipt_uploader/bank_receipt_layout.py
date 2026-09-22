"""Shared bank receipt layout; category never controls upload eligibility."""
from pathlib import Path
from typing import Mapping, Any

CATEGORIES = ("manual", "automatic")


def receipt_paths(root: Path) -> list[Path]:
    """Include legacy flat receipts and both semantic categories."""
    return sorted(root.rglob("receipt_*/receipt.json"))


def receipt_path(output_root: Path, key: str, record: Mapping[str, Any]) -> Path:
    candidate = output_root / f"receipt_{key}"
    if candidate.parent != output_root or key in {"", ".", ".."}:
        raise ValueError("Invalid bank receipt key")
    existing_paths = [base / f"receipt_{key}" / "receipt.json"
                      for base in (output_root, output_root / "manual", output_root / "automatic")
                      if (base / f"receipt_{key}" / "receipt.json").is_file()]
    if len(existing_paths) > 1:
        raise ValueError(f"receipt在多个目录重复，禁止生成或上传：{key}")
    source_receipt = record.get("receipt") or {}
    source_pdf = str(source_receipt.get("pdf") or "")
    from .bank_rules import manual_bank_remark
    requires_manual = bool(manual_bank_remark(record.get("remark")))
    category = "automatic" if not requires_manual and source_pdf and Path(source_pdf).is_file() else "manual"
    if existing_paths and requires_manual and existing_paths[0].parent.parent != output_root / "manual":
        import shutil
        old_folder = existing_paths[0].parent
        target_folder = output_root / "manual" / old_folder.name
        old_folder.resolve().relative_to(output_root.resolve())
        target_folder.resolve().relative_to(output_root.resolve())
        if target_folder.exists():
            raise ValueError(f"manual目标已存在：{target_folder}")
        shutil.move(str(old_folder), str(target_folder))
        existing_paths = [target_folder / "receipt.json"]
    return existing_paths[0] if existing_paths else output_root / category / f"receipt_{key}" / "receipt.json"
