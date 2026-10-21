"""The actual native writer expires only owned stale logs off the game thread."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
HOST = r'''
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static DWORD main_thread;
static volatile LONG checked, wrong_thread;
static BOOL WINAPI observed_attributes(LPCWSTR path, GET_FILEEX_INFO_LEVELS level, LPVOID result) {
    InterlockedIncrement(&checked);
    if (GetCurrentThreadId() == main_thread) InterlockedIncrement(&wrong_thread);
    return GetFileAttributesExW(path, level, result);
}
static void self_dir(wchar_t *out, size_t count) {
    GetCurrentDirectoryW((DWORD)count, out);
    wcsncat_s(out, count, L"\\", _TRUNCATE);
}
#define GetFileAttributesExW observed_attributes
#include "diagnostics.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "retention line %d\n", __LINE__); return 1; } } while (0)
static BOOL put(const wchar_t *path, int stale) {
    HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return FALSE;
    DWORD count;
    BOOL result = WriteFile(file, "old-marker\n", 11, &count, NULL);
    if (stale) {
        FILETIME clock;
        ULARGE_INTEGER stamp;
        GetSystemTimeAsFileTime(&clock);
        stamp.LowPart = clock.dwLowDateTime; stamp.HighPart = clock.dwHighDateTime;
        stamp.QuadPart -= ((ULONGLONG)LOG_MAX_AGE_SECONDS + 1u) * 10000000u;
        clock.dwLowDateTime = stamp.LowPart; clock.dwHighDateTime = stamp.HighPart;
        result = result && SetFileTime(file, NULL, NULL, &clock);
    }
    CloseHandle(file);
    return result;
}
int main(void) {
    main_thread = GetCurrentThreadId();
    CHECK(put(L"sm64_trainer_gfx.log", 1));
    CHECK(put(L"sm64_trainer_gfx.log.1", 1));
    CHECK(put(L"sm64_trainer_gfx.log.2", 0));
    CHECK(put(L"sm64_trainer_gfx.activation.log", 1));
    CHECK(put(L"sm64_trainer_gfx.activation.log.1", 1));
    CHECK(put(L"personal.log", 1));
    diagnostics_start();
    CHECK(g_log != NULL);
    plugin_logf("rom_open_begin", "fresh=1");
    HANDLE writer;
    CHECK(DuplicateHandle(GetCurrentProcess(), g_log->thread, GetCurrentProcess(),
        &writer, SYNCHRONIZE, FALSE, 0));
    diagnostics_stop();
    CHECK(WaitForSingleObject(writer, 5000) == WAIT_OBJECT_0);
    CloseHandle(writer);
    CHECK(checked >= 5 && wrong_thread == 0);
    CHECK(GetFileAttributesW(L"sm64_trainer_gfx.log.1") == INVALID_FILE_ATTRIBUTES);
    CHECK(GetFileAttributesW(L"sm64_trainer_gfx.activation.log.1") == INVALID_FILE_ATTRIBUTES);
    CHECK(GetFileAttributesW(L"sm64_trainer_gfx.log.2") != INVALID_FILE_ATTRIBUTES);
    CHECK(GetFileAttributesW(L"personal.log") != INVALID_FILE_ATTRIBUTES);
    return 0;
}
'''


def test_native_writer_prunes_stale_owned_files_without_callback_io(tmp_path):
    spec = importlib.util.spec_from_file_location("retention_native_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if not build.toolchain_available():
        pytest.skip("no x86 MSVC toolchain")
    source = tmp_path / "retention.c"
    source.write_text(HOST, encoding="ascii")
    target = tmp_path / "retention.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(source), f"/I{ROOT / 'plugin/gfxwrap'}", f"/Fe:{target}",
        f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=10, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("sm64_trainer_gfx.log", "sm64_trainer_gfx.activation.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "old-marker" not in text and "fresh=1" in text
    assert (tmp_path / "sm64_trainer_gfx.log.2").read_text() == "old-marker\n"
    assert (tmp_path / "personal.log").read_text() == "old-marker\n"
