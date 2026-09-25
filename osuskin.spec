# Compatibility alias: both historical build commands now use the same spec.
from pathlib import Path

canonical_spec = Path(SPECPATH) / 'OsuSkinEditor.spec'
exec(compile(canonical_spec.read_text(encoding='utf-8'), str(canonical_spec), 'exec'))
