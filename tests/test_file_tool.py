import pytest

from app.tools.file_tool import FileReadError, FileTool, FileWriteError


def test_read_text_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")

    fc = FileTool().read(p)

    assert fc.content == "hello\nworld\n"
    assert fc.line_count == 2
    assert fc.encoding == "utf-8"


def test_read_nonexistent_raises(tmp_path):
    with pytest.raises(FileReadError):
        FileTool().read(tmp_path / "nope.txt")


def test_read_directory_raises(tmp_path):
    with pytest.raises(FileReadError):
        FileTool().read(tmp_path)


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / "out.txt"

    result = FileTool().write(p, "你好")

    assert result.created is True
    assert result.bytes_written == 6
    assert FileTool().read(p).content == "你好"


def test_write_no_overwrite_raises(tmp_path):
    p = tmp_path / "exist.txt"
    p.write_text("x", encoding="utf-8")

    with pytest.raises(FileWriteError):
        FileTool().write(p, "y", overwrite=False)


def test_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "c.txt"

    FileTool().write(p, "deep")

    assert p.read_text(encoding="utf-8") == "deep"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a\nb\nc\n", 3),
        ("", 0),
        ("single line", 1),
        ("no newline at end", 1),
        ("a\nb", 2),
    ],
)
def test_line_count(tmp_path, text, expected):
    p = tmp_path / "f.txt"
    p.write_text(text, encoding="utf-8")

    assert FileTool().read(p).line_count == expected

def test_list_dir_returns_entries(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    entries = FileTool().list_dir(tmp_path)
    names = {entry.name for entry in entries}

    assert "a.txt" in names
    assert "sub" in names

    sub = next(entry for entry in entries if entry.name == "sub")
    assert sub.is_dir is True
    assert sub.size == 0


def test_list_dir_recursive_pattern(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text("x", encoding="utf-8")
    (tmp_path / "a" / "b" / "c.txt").write_text("y", encoding="utf-8")

    entries = FileTool().list_dir(tmp_path, recursive=True, pattern="*.py")

    assert any(entry.name == "c.py" for entry in entries)
    assert not any(entry.name == "c.txt" for entry in entries)


def test_list_dir_on_file_raises(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x", encoding="utf-8")

    with pytest.raises(FileReadError):
        FileTool().list_dir(p)

def test_append_adds_content_to_existing_file(tmp_path):
    p = tmp_path / "log.txt"
    tool = FileTool()

    tool.write(p, "A\n")
    result = tool.append(p, "B\n")

    assert result.created is False
    assert result.bytes_written == 2
    assert tool.read(p).content == "A\nB\n"


def test_append_creates_file_if_missing(tmp_path):
    p = tmp_path / "new.txt"
    result = FileTool().append(p, "hello")

    assert result.created is True
    assert result.bytes_written == 5
    assert FileTool().read(p).content == "hello"


def test_read_lines_returns_lines_without_newlines(tmp_path):
    p = tmp_path / "lines.txt"
    p.write_text("a\nb\nc\n", encoding="utf-8")

    assert FileTool().read_lines(p) == ["a", "b", "c"]

def test_read_auto_detects_gbk_encoding(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("你好".encode("gbk"))

    fc = FileTool().read(p, encoding=None)

    assert fc.content == "你好"
    assert fc.line_count == 1
    assert fc.size == 4
    assert fc.encoding is not None

def test_write_and_read_bytes_roundtrip(tmp_path):
    p = tmp_path / "data.bin"
    data = b"\x00\x01abc"

    result = FileTool().write_bytes(p, data)

    assert result.created is True
    assert result.bytes_written == len(data)
    assert FileTool().read_bytes(p) == data


def test_write_bytes_no_overwrite_raises(tmp_path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"old")

    with pytest.raises(FileWriteError):
        FileTool().write_bytes(p, b"new", overwrite=False)


def test_batch_prints_start_and_finish(capsys):
    tool = FileTool()

    with tool.batch():
        print("inside batch")

    captured = capsys.readouterr()

    assert "Batch started." in captured.out
    assert "inside batch" in captured.out
    assert "Batch finished." in captured.out