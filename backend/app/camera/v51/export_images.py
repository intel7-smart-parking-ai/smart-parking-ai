"""Human-reviewed visualization; never modifies training originals."""
from pathlib import Path
from capture_views import view_folder,view_code
import hashlib
import io
from collection_output import atomic_bytes
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from parking_ai.storage import safe_path


def write_images(root, folder, record, rows, image):
    approved = [r for r in rows if r['review_status'] == 'approved']
    if not approved:
        return None
    folder = Path(folder)
    original = folder / 'originals' / view_folder(record) / (record['image_id'] + '.png')
    preview = folder / 'previews' / view_folder(record) / (record['image_id'] + '.png')
    payload = safe_path(root, record['raw_path']).read_bytes()
    decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or not np.array_equal(decoded, image):
        raise ValueError('내보내기 도중 원본 변경: 다시 시도하세요.')
    digest = hashlib.sha256(payload).hexdigest()
    if record.get('raw_sha256', digest) != digest:
        raise ValueError('원본 SHA-256 불일치')
    original.parent.mkdir(parents=True, exist_ok=True)
    preview.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(original,payload)
    h, w = image.shape[:2]
    font = ImageFont.load_default(size=max(16, min(28, w // 65)))
    line = max(26, int(font.size * 1.6))
    canvas = Image.new('RGB', (max(w, 1000), h + line * (len(approved) + 3)), '#16202b')
    canvas.paste(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)), (0, 0))
    draw = ImageDraw.Draw(canvas)
    virtual=record['roi'].get('registration_method')=='virtual_sticker_guide_v1'
    polygons = {s['space_id']: s['polygon'] for s in record['roi']['spaces']}
    for i, row in enumerate(approved, 1):
        points = [tuple(p) for p in polygons[row['space_id']]]
        draw.line([points[3], *points[:3]] if virtual else points + points[:1], fill='#369bff' if virtual else '#00ff40', width=3)
        x, y = points[0]
        label = f"{i}: {record['lot_id']} / {row['space_id']}"
        box = draw.textbbox((x, y), label, font=font)
        draw.rectangle(box, fill='black'); draw.text((x, y), label, font=font, fill='white')
        if row.get('sticker_polygon'):
            pts = [tuple(p) for p in row['sticker_polygon']]
            draw.line(pts + pts[:1], fill='orange', width=3)
        if row.get('plate_bbox'):
            draw.rectangle(tuple(row['plate_bbox']), outline='cyan', width=2)
        text = f"{i}. {record['lot_id']} / {row['space_id']} | {row['occupancy']} | number: {row['plate_text'] or 'unknown'} | sticker: {row['color']} | type: {row.get('vehicle_type') or 'unknown'}"
        draw.text((12, h + line * (i + 1)), text, font=font, fill='white')
    draw.text((12, h + 4), 'HUMAN REVIEWED LABELS - NOT AUTOMATIC OCR RESULTS', font=font, fill='#ffdc70')
    legend="Blue: virtual sticker placement (physical bay unknown)" if virtual else "Green: parking ROI"
    draw.text((12, h + line), legend+" / Orange: sticker interior / Cyan: number box.", font=font, fill='white')
    buffer=io.BytesIO();canvas.save(buffer,format='PNG');preview_digest=atomic_bytes(preview,buffer.getvalue())
    return {'capture_view':view_code(record),'image_id': record['image_id'], 'original': original.relative_to(folder).as_posix(),
            'preview': preview.relative_to(folder).as_posix(), 'raw_sha256': digest,'preview_sha256':preview_digest}
