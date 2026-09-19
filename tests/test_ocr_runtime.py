"""Optional real local inference test; no service, login or business documents."""
import os

import pytest


@pytest.mark.skipif(os.environ.get('KDZWY_OCR_SMOKE') != '1', reason='real OCR smoke runs in the full-runtime CI job')
def test_installed_ocr_engine_recognizes_synthetic_digits():
    import cv2
    import numpy as np
    from kdzwy_receipt_uploader.ocr.engine import _get_ocr_engine

    canvas = np.full((180, 650, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, '12345678', (40, 110), cv2.FONT_HERSHEY_SIMPLEX, 2.3, (0, 0, 0), 3)
    rows, _ = _get_ocr_engine()(canvas)
    assert rows, 'OCR returned no text for the synthetic image'
    assert '12345678' in ''.join(str(row[1]).replace(' ', '') for row in rows)
