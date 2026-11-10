"""The photo endpoint and offline evaluation use the same oriented RGB pixels."""
import io
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000


def decode_image(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('사진은 비어 있지 않은 16MB 이하 파일이어야 합니다.')
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ('JPEG','PNG','WEBP'):
                raise ValueError('JPG·PNG·WEBP 사진을 선택하세요.')
            if original.width * original.height > MAX_PIXELS or getattr(original,'n_frames',1) != 1:
                raise ValueError('사진은 1,600만 픽셀 이하의 정지 이미지여야 합니다.')
            return ImageOps.exif_transpose(original).convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('사진 파일을 읽을 수 없습니다.') from error
