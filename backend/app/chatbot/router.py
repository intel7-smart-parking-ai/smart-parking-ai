"""Team entry point for /api/v1/chat, with a private runtime package name."""
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

CHATBOT_ROOT = Path(__file__).resolve().parent / "parking-chatbot"
_PACKAGE = f"{__package__}._parking_runtime"


def _load_router():
    package_path = CHATBOT_ROOT / "chatbot"
    existing = sys.modules.get("chatbot")
    if existing is not None:
        origin = getattr(existing, "__file__", None)
        if not origin or Path(origin).resolve() != package_path / "__init__.py":
            raise ImportError("parking-chatbot: a different top-level 'chatbot' package is already loaded")
    if _PACKAGE in sys.modules:
        return import_module(f"{_PACKAGE}.http_router").router
    spec = spec_from_file_location(_PACKAGE, package_path / "__init__.py",
                                   submodule_search_locations=[str(package_path)])
    package = module_from_spec(spec)
    sys.modules[_PACKAGE] = package
    try:
        spec.loader.exec_module(package)
        return import_module(f"{_PACKAGE}.http_router").router
    except BaseException:
        for name in list(sys.modules):
            if name == _PACKAGE or name.startswith(_PACKAGE + "."):
                sys.modules.pop(name, None)
        raise


router = _load_router()
__all__ = ["router", "CHATBOT_ROOT"]
