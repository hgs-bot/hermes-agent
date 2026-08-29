import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const smokeUrl = new URL("../smoke/windows-local-runtime-smoke.ps1", import.meta.url);

test("Windows smoke is isolated and never downloads local-runtime assets", async () => {
  const source = await readFile(smokeUrl, "utf8");

  assert.match(source, /hermes\.exe.*local.*--help/is);
  assert.match(source, /hermes-local-smoke-/);
  assert.match(source, /HERMES_HOME/);
  assert.doesNotMatch(source, /\blocal\s+pull\b/i);
  assert.doesNotMatch(source, /Invoke-WebRequest|Start-BitsTransfer/i);
});

test("Windows smoke verifies pinned CUDA metadata, cache invalidation, and split logs", async () => {
  const source = await readFile(smokeUrl, "utf8");

  for (const contract of [
    "load_builtin_manifest",
    "is_runtime_cache_complete",
    '"win32"',
    '"amd64"',
    '"cuda"',
    '"build"',
    '"cuda-runtime"',
    "ggml-cuda.dll",
    "cudart64_12.dll",
    "stdout_log",
    "stderr_log",
  ]) {
    assert.ok(source.includes(contract), `missing smoke contract: ${contract}`);
  }

  assert.match(source, /unlink\(\).*is_runtime_cache_complete/s);
  assert.match(source, /sha256.*64/s);
});