"""gph - reader/writer for Magneti Marelli GPH graphic database files.

Layering:

* :mod:`gph.container` - the byte-exact container: header, sections, item index.
* :mod:`gph.schema`    - declarative field layouts per section type (``MENU2006`` ...).
* :mod:`gph.decode`    - turns raw item bytes into dicts using the schemas.
* :mod:`gph.textures`  - decodes ``TXUR2008`` texture payloads to Pillow images.
* :mod:`gph.fonts`     - decodes ``BFNT2000`` bitmap fonts.
* :mod:`gph.workspace` - a set of related files (common, language, font, theme) with link resolution.
* :mod:`gph.inf`       - the ``.inf`` side-car files.
"""

from .container import GphFile, Section
from .decode import decode_item, DecodedItem
from .workspace import Workspace

__all__ = ["GphFile", "Section", "decode_item", "DecodedItem", "Workspace"]
__version__ = "0.1.0"
