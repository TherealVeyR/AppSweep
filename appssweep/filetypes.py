"""Conservative file-type labels based only on filename extensions.

AppSweep never reads file contents to label a file. An unfamiliar or absent
extension is shown as unknown or by its extension, never hidden.
"""

from __future__ import annotations

from pathlib import Path

from .models import FileKind, FileType

# Longest suffixes first so .tar.gz is not read as .gz.
_KNOWN: dict[str, tuple[str, FileKind, str]] = {
    ".tar.gz": ("TAR.GZ", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".tar.xz": ("TAR.XZ", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".tar.zst": ("TAR.ZST", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".tar.bz2": ("TAR.BZ2", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".qcow2": ("QCOW2", FileKind.DISK_IMAGE, "a QEMU virtual machine disk image"),
    ".vmdk": ("VMDK", FileKind.DISK_IMAGE, "a VMware virtual machine disk image"),
    ".vdi": ("VDI", FileKind.DISK_IMAGE, "a VirtualBox virtual machine disk image"),
    ".vhd": ("VHD", FileKind.DISK_IMAGE, "a virtual hard disk image"),
    ".vhdx": ("VHDX", FileKind.DISK_IMAGE, "a virtual hard disk image"),
    ".img": ("IMG", FileKind.DISK_IMAGE, "a raw disk image"),
    ".iso": ("ISO", FileKind.DISK_IMAGE, "an optical disc image, often an installer"),
    ".raw": ("RAW", FileKind.DISK_IMAGE, "a raw disk image"),
    ".appimage": ("APPIMAGE", FileKind.PACKAGE, "a portable Linux application package"),
    ".flatpakref": ("FLATPAKREF", FileKind.PACKAGE, "a Flatpak application reference file"),
    ".flatpak": ("FLATPAK", FileKind.PACKAGE, "a Flatpak application bundle"),
    ".rpm": ("RPM", FileKind.PACKAGE, "a Fedora/RHEL software package"),
    ".deb": ("DEB", FileKind.PACKAGE, "a Debian software package"),
    ".msi": ("MSI", FileKind.INSTALLER, "a Windows installer package"),
    ".exe": ("EXE", FileKind.INSTALLER, "a Windows executable or installer"),
    ".dmg": ("DMG", FileKind.INSTALLER, "a macOS disk image"),
    ".apk": ("APK", FileKind.INSTALLER, "an Android application package"),
    ".mp4": ("MP4", FileKind.VIDEO, "a digital video file"),
    ".mkv": ("MKV", FileKind.VIDEO, "a digital video file"),
    ".mov": ("MOV", FileKind.VIDEO, "a digital video file"),
    ".avi": ("AVI", FileKind.VIDEO, "a digital video file"),
    ".webm": ("WEBM", FileKind.VIDEO, "a digital video file"),
    ".m4v": ("M4V", FileKind.VIDEO, "a digital video file"),
    ".flv": ("FLV", FileKind.VIDEO, "a digital video file"),
    ".wmv": ("WMV", FileKind.VIDEO, "a digital video file"),
    ".mpg": ("MPG", FileKind.VIDEO, "a digital video file"),
    ".mpeg": ("MPEG", FileKind.VIDEO, "a digital video file"),
    ".mp3": ("MP3", FileKind.AUDIO, "an audio file"),
    ".flac": ("FLAC", FileKind.AUDIO, "a lossless audio file"),
    ".wav": ("WAV", FileKind.AUDIO, "an uncompressed audio file"),
    ".ogg": ("OGG", FileKind.AUDIO, "an audio file"),
    ".m4a": ("M4A", FileKind.AUDIO, "an audio file"),
    ".opus": ("OPUS", FileKind.AUDIO, "an audio file"),
    ".zip": ("ZIP", FileKind.ARCHIVE, "a compressed archive"),
    ".7z": ("7Z", FileKind.ARCHIVE, "a compressed archive"),
    ".rar": ("RAR", FileKind.ARCHIVE, "a compressed archive"),
    ".tar": ("TAR", FileKind.ARCHIVE, "an uncompressed archive"),
    ".tgz": ("TGZ", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".tbz2": ("TBZ2", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".txz": ("TXZ", FileKind.ARCHIVE, "a compressed tarball archive"),
    ".gz": ("GZ", FileKind.ARCHIVE, "a gzip-compressed file"),
    ".xz": ("XZ", FileKind.ARCHIVE, "an xz-compressed file"),
    ".zst": ("ZST", FileKind.ARCHIVE, "a zstd-compressed file"),
    ".bz2": ("BZ2", FileKind.ARCHIVE, "a bzip2-compressed file"),
    ".jpg": ("JPG", FileKind.PICTURE, "a picture"),
    ".jpeg": ("JPEG", FileKind.PICTURE, "a picture"),
    ".png": ("PNG", FileKind.PICTURE, "a picture"),
    ".gif": ("GIF", FileKind.PICTURE, "an image"),
    ".webp": ("WEBP", FileKind.PICTURE, "a picture"),
    ".heic": ("HEIC", FileKind.PICTURE, "a picture"),
    ".svg": ("SVG", FileKind.PICTURE, "a vector image"),
    ".pdf": ("PDF", FileKind.DOCUMENT, "a PDF document"),
    ".doc": ("DOC", FileKind.DOCUMENT, "a word-processing document"),
    ".docx": ("DOCX", FileKind.DOCUMENT, "a word-processing document"),
    ".odt": ("ODT", FileKind.DOCUMENT, "an office document"),
    ".xls": ("XLS", FileKind.DOCUMENT, "a spreadsheet"),
    ".xlsx": ("XLSX", FileKind.DOCUMENT, "a spreadsheet"),
    ".ods": ("ODS", FileKind.DOCUMENT, "a spreadsheet"),
    ".odp": ("ODP", FileKind.DOCUMENT, "a presentation"),
    ".pptx": ("PPTX", FileKind.DOCUMENT, "a presentation"),
}

_MULTI_SUFFIXES = tuple(sorted(_KNOWN, key=len, reverse=True))


def classify_path(path: str) -> FileType:
    name = Path(path).name.lower()
    for suffix in _MULTI_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            label, kind, description = _KNOWN[suffix]
            return FileType(
                extension=suffix,
                label=label,
                kind=kind,
                description=f"Looks like {description} (based on the {suffix} extension).",
            )

    suffix = Path(name).suffix.lower()
    if suffix:
        return FileType(
            extension=suffix,
            label="UNKNOWN",
            kind=FileKind.UNKNOWN,
            description=(
                f"Unrecognized file type ({suffix}); AppSweep did not inspect the contents."
            ),
        )
    return FileType(
        extension="",
        label="UNKNOWN",
        kind=FileKind.UNKNOWN,
        description="No filename extension; file type unknown. Contents were not inspected.",
    )


def kind_label(kind: FileKind) -> str:
    return kind.value


def is_archive_or_disk_image(kind: FileKind) -> bool:
    return kind in {FileKind.ARCHIVE, FileKind.DISK_IMAGE}
