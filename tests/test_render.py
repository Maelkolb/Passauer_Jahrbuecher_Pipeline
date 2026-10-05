"""Source handling in ``pjb_pipeline.render``: book PDF vs. a folder of
individually scanned page images."""

import json

import pytest
from PIL import Image

from pjb_pipeline import render
from pjb_pipeline.config import VolumeConfig


def _img(path, size=(3000, 4400), color=(240, 240, 230), **save):
    Image.new("RGB", size, color).save(path, **save)
    return path


def _cfg(tmp_path, source, **kw):
    return VolumeConfig(volume_number=66, volume_number_roman="LXVI", volume_year=2024,
                        source_path=str(source), output_root=str(tmp_path / "out"), **kw)


def test_natural_page_order_and_downscaling(tmp_path):
    src = tmp_path / "scans"
    src.mkdir()
    for name in ("page_10.jpg", "page_2.png", "page_1.tif"):
        _img(src / name)
    (src / "Thumbs.db").write_bytes(b"")
    cfg = _cfg(tmp_path, src)
    pages = render.run(cfg)
    assert [p["source"] for p in pages] == ["page_1.tif", "page_2.png", "page_10.jpg"]
    assert [p["page_num"] for p in pages] == [1, 2, 3]
    assert max(pages[0]["width"], pages[0]["height"]) == 2200
    assert (cfg.pages_dir / "page_0003.png").exists()
    index = json.loads((cfg.logs_dir / "pages_index.json").read_text())
    assert len(index) == 3


def test_exif_rotation_is_applied(tmp_path):
    src = tmp_path / "scans"
    src.mkdir()
    im = Image.new("RGB", (400, 300), (255, 255, 255))
    exif = Image.Exif()
    exif[0x0112] = 6          # rotate 90° CW on display
    im.save(src / "p1.jpg", exif=exif)
    pages = render.run(_cfg(tmp_path, src, image_max_side=None))
    assert (pages[0]["width"], pages[0]["height"]) == (300, 400)


def test_multipage_tiff_gives_one_page_per_frame(tmp_path):
    src = tmp_path / "scans"
    src.mkdir()
    frames = [Image.new("RGB", (500, 700), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(src / "bundle.tif", save_all=True, append_images=frames[1:])
    pages = render.run(_cfg(tmp_path, src, image_max_side=None))
    assert len(pages) == 2
    assert pages[1]["source_frame"] == 1


def test_page_range_and_reuse(tmp_path):
    src = tmp_path / "scans"
    src.mkdir()
    for i in range(1, 6):
        _img(src / f"{i:03d}.png", size=(800, 1100))
    cfg = _cfg(tmp_path, src, page_range=(2, 4))
    pages = render.run(cfg)
    assert [p["page_num"] for p in pages] == [2, 3, 4]
    mtime = (cfg.pages_dir / "page_0002.png").stat().st_mtime_ns
    render.run(cfg)
    assert (cfg.pages_dir / "page_0002.png").stat().st_mtime_ns == mtime


def test_scanner_folder_with_image_subfolder(tmp_path):
    root = tmp_path / "2026-06-15_10-00_LXVI_2024_finished"
    (root / "jpg").mkdir(parents=True)
    _img(root / "jpg" / "0001.jpg", size=(600, 800))
    kind, path, images = render.resolve_source(root)
    assert kind == "images" and path == root / "jpg" and len(images) == 1


def test_scanner_folder_with_pdf(tmp_path):
    root = tmp_path / "finished"
    (root / "pdf").mkdir(parents=True)
    (root / "pdf" / "book.pdf").write_bytes(b"%PDF-1.4\n")
    kind, path, _ = render.resolve_source(root)
    assert kind == "pdf" and path.name == "book.pdf"


def test_missing_source_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        render.resolve_source(tmp_path / "nope")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        render.resolve_source(empty)


def test_pdf_path_is_still_accepted():
    cfg = VolumeConfig(pdf_path="/x/book.pdf", volume_number=1, volume_number_roman="I",
                       volume_year=1959)
    assert cfg.source == "/x/book.pdf" and cfg.source_path == "/x/book.pdf"


def test_bundle_follows_symlinked_pages(tmp_path):
    import os
    import zipfile
    from pjb_pipeline.pipeline import bundle_output
    scans = tmp_path / "elsewhere"
    scans.mkdir()
    (scans / "page_0001.png").write_bytes(b"png")
    cfg = VolumeConfig(volume_number=56, volume_number_roman="LVI", volume_year=2014,
                       source_path="x.pdf", output_root=str(tmp_path / "out"))
    cfg.out_dir.mkdir(parents=True)
    os.symlink(scans, cfg.out_dir / "pages")
    (cfg.out_dir / "tei").mkdir()
    (cfg.out_dir / "tei" / "x.xml").write_text("<TEI/>")
    names = zipfile.ZipFile(bundle_output(cfg)).namelist()
    assert "pjb-056-2014/pages/page_0001.png" in names
    assert "pjb-056-2014/tei/x.xml" in names
