# Disable mimalloc so srtla_send runs on CPUs without POPCNT/AVX (Core 2 Duo kits).
from pathlib import Path
import re

cargo = Path("Cargo.toml")
text = cargo.read_text(encoding="utf-8")
text = re.sub(r"\nmimalloc = \{.*?\}\n", "\n", text, count=1, flags=re.S)
cargo.write_text(text, encoding="utf-8")

main = Path("src/main.rs")
src = main.read_text(encoding="utf-8")
src = src.replace(
    "// Use mimalloc as the global allocator for the binary (non-Windows only)\n"
    "#[cfg(not(windows))]\n"
    "#[global_allocator]\n"
    "static ALLOC: mimalloc::MiMalloc = mimalloc::MiMalloc;\n\n",
    "",
)
if "mimalloc" in src or "mimalloc" in cargo.read_text(encoding="utf-8"):
    raise SystemExit("failed to strip mimalloc")
main.write_text(src, encoding="utf-8")
print("stripped mimalloc from srtla_send")
