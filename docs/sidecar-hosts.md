# Running the sidecar from a host application

This page is guidance for authors of desktop and native applications that drive
[`pydiggs serve --stdio`](sidecar.md). It covers process supervision, pool sizing,
memory caps, packaging and example clients. The protocol itself is specified in
[Stdio sidecar protocol](sidecar.md).

!!! warning "Findings are untrusted text"
    Every string field in a findings document can carry text from the validated file.
    HTML-encode or otherwise escape it before it reaches a web view, a rich-text
    control, a terminal or a log. Never build markup or commands from it.

## Process supervision

- **Spawn** the sidecar with stdin and stdout as pipes. Give stderr a reader that drains
  it continuously, or redirect it to a log file or the null device. An undrained stderr
  pipe (often 64 KiB) eventually blocks the sidecar.
- **Hide the console** on Windows: `CREATE_NO_WINDOW` (`CreateNoWindow = true` in .NET,
  `windowsHide: true` in Node, `creation_flags(0x08000000)` with
  `std::os::windows::process::CommandExt` in Rust).
- **Wait for `initialize`** before showing the validator as ready. A cold start (process
  launch plus warm-up) takes up to about 3 seconds; requests may be pipelined behind
  `initialize`.
- **Read stdout with a streaming line reader** that has no fixed maximum: one result can
  be several megabytes on a single line. Split on LF only.
- **Match responses by `id`.** Cheap methods overtake running validations.
- **Own the hard timeout.** `time_budget_s` and `$/cancelRequest` act only between
  validation steps. When a validation exceeds your limit, kill the process (and its
  process tree), show the file as **"not checked"** (never as passed), and start a new
  sidecar for the next file.
- **Restart with backoff** when the process exits unexpectedly (for example 0.5 s, 1 s,
  2 s, capped at 30 s; reset after a minute of healthy running). Exit status 2 (bad
  arguments) and 3 (unusable bundled data) will not fix themselves: report them instead
  of looping.
- **Shut down cleanly** with `shutdown`, then `exit`, then wait for the process. Closing
  stdin also works (status 0) and abandons any running validation. As a last resort,
  SIGTERM (or Ctrl+Break on Windows) ends it with status 0.
- **Tie the child's lifetime to the host's** so a crashed host does not leave sidecars
  behind: `PR_SET_PDEATHSIG` on Linux (set between fork and exec, e.g. via a small
  launcher or `pre_exec` in Rust), a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
  on Windows, and on macOS a host-side watchdog or relying on stdin EOF (the sidecar exits
  when its stdin closes, which happens when the host dies).

## Pool sizing

One sidecar validates one document at a time and queues at most one more. For
parallelism, run a pool:

- Size it at `N = clamp(cores - 1, 1, 4)` and start the processes ahead of time so each
  is warm before the first file arrives.
- Dispatch one validation per idle process. On `busy` (-32000) pick another process or
  retry after the current one finishes.
- Budget about **2 GiB of memory per process** (see below), so memory, not cores, often
  limits `N` on small machines.
- Recycle a process after a hard timeout or a crash; optionally also after a few hundred
  documents to return memory to the OS.

## Memory caps

A sidecar peaks at roughly 0.5 GiB for a 25 MiB document sent as content and about
1.5 GiB for a dense 100 MiB document read by path. Cap each process from the host:

| OS | mechanism |
|---|---|
| Linux | `setrlimit(RLIMIT_AS, 2 GiB)` and `RLIMIT_CPU` in the child before exec (or a cgroup / systemd scope); the kernel enforces both |
| macOS | `RLIMIT_CPU` before exec; `RLIMIT_AS` is not enforced by the kernel, so poll the child's resident size (`proc_pidinfo` / `ps -o rss`) and kill it past your limit |
| Windows | assign the process to a Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` (2 GiB) and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` |

A process killed by a cap is handled like a hard timeout: the file is "not checked".

## Packaging

Pick one way to ship Python with your application:

- **PyInstaller ≥ 6.22.3, `--onedir`** (recommended). Build a console executable (the
  console bootloader keeps stdio working) and hide its window from the host instead of
  using windowed mode, which can leave stdin and stdout unset on Windows. Call
  `multiprocessing.freeze_support()` first in the entry script (the `pydiggs` CLI already
  does). Avoid `--onefile`: it unpacks to a temporary folder on every start, which slows
  the cold start and trips some endpoint-security tools. Sign the bundle inside-out (every
  library, then the executable, then the app) and notarise it on macOS. Bundlers that
  expect a per-platform suffix can name the executable `pydiggs-$TARGET_TRIPLE`.

  ```python
  # sidecar_main.py, built with: pyinstaller --onedir --console --name pydiggs sidecar_main.py
  import multiprocessing
  import sys

  if __name__ == "__main__":
      multiprocessing.freeze_support()
      from pydiggs.cli import main

      sys.exit(main(["serve", "--stdio", *sys.argv[1:]]))
  ```

  PyInstaller must collect pydiggs's data files (schemas, dictionaries, Schematron):
  add `--collect-data pydiggs`.

- **Nuitka 4.3.4** (`--standalone`, `--include-package-data=pydiggs`) as an alternative
  that compiles to C; the same console and signing advice applies.
- **A uv-managed runtime**: ship `uv` and run `uv tool run --python 3.12 --from
  pydiggs==X.Y.Z pydiggs serve --stdio` (or install into an app-private environment on
  first start). Pin pydiggs and Python to exact versions so every user runs the tested
  combination.

Whichever you choose, check `initialize.result.pydiggs_version` and
`findings_schema_version` at start and log them with your own version.

## Example clients

Each example starts the sidecar, initialises it, validates one file sent as base64
content, shows a refused by-path request, then shuts down. Each was run against a
development build of pydiggs. They are deliberately small: production code adds
restart, timeouts per request and a pool.

### TypeScript (Node.js)

Runs directly with Node.js 22.6 or later (type stripping); in an Electron app run it in
the main process, or in a `utilityProcess` to keep the main process responsive.

```typescript
// pydiggs sidecar client for Node.js (TypeScript). Node >= 22.6 runs this file directly.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createInterface } from "node:readline";

type Pending = { resolve: (v: any) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };

export class SidecarError extends Error {
  code: number;
  slug: string; // error.data.code: switch on this
  data: any;
  constructor(code: number, slug: string, message: string, data: any) {
    super(message);
    this.code = code;
    this.slug = slug;
    this.data = data;
  }
}

export class Sidecar {
  private proc: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private pending = new Map<number, Pending>();

  constructor(command: string, args: string[] = ["serve", "--stdio"]) {
    // stderr must be drained or redirected: an undrained pipe blocks the sidecar.
    this.proc = spawn(command, args, { stdio: ["pipe", "pipe", "inherit"], windowsHide: true });
    // readline has no fixed line limit, so multi-megabyte result lines are fine.
    const lines = createInterface({ input: this.proc.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => this.onLine(line));
    this.proc.on("exit", (code) => {
      for (const p of this.pending.values()) p.reject(new Error(`sidecar exited (${code})`));
      this.pending.clear();
    });
  }

  private onLine(line: string): void {
    const msg = JSON.parse(line);
    if (msg.id === undefined) return; // a notification such as $/progress
    const p = this.pending.get(msg.id);
    if (!p) return;
    this.pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.error) {
      const { code, message, data } = msg.error;
      p.reject(new SidecarError(code, data?.code, message, data));
    } else {
      p.resolve(msg.result);
    }
  }

  request<T = any>(method: string, params: object = {}, timeoutMs = 120_000): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      // The host owns hard limits: on timeout, kill the process (respawn it for the next file).
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.proc.kill();
        reject(new Error(`${method} timed out: file not checked`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }

  async close(): Promise<void> {
    await this.request("shutdown");
    this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "exit" }) + "\n");
    this.proc.stdin.end();
  }
}

// Usage: node client.ts <pydiggs executable> <file.xml>
const [command, file] = process.argv.slice(2);
const sidecar = new Sidecar(command);
const info = await sidecar.request("initialize", {
  protocol_version: "1.0",
  client_info: { name: "example-node-host", version: "0.1" },
});
console.log("pydiggs", info.pydiggs_version, "protocol", info.protocol_version);
const content = (await readFile(file)).toString("base64");
const doc = await sidecar.request("validate", { content_base64: content, name: file });
for (const entry of doc.files) {
  // Every string in a findings document is untrusted: escape it before display.
  console.log(entry.path, entry.verdict, entry.findings.length, "findings");
}
try {
  await sidecar.request("validate", { path: "relative.xml" });
} catch (e) {
  if (e instanceof SidecarError) console.log("refused:", e.slug);
}
await sidecar.close();
```

### Rust

`Cargo.toml` dependencies:

```toml
[package]
name = "sidecar-client"
version = "0.1.0"
edition = "2021"

[dependencies]
serde_json = "1"
base64 = "0.22"
```

```rust
// pydiggs sidecar client for Rust: std::process + serde_json (+ base64).
use base64::Engine;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

struct Sidecar {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
}

#[derive(Debug)]
#[allow(dead_code)] // fields are read through Debug
enum SidecarError {
    Rpc { code: i64, slug: String, message: String },
    Io(std::io::Error),
    Closed,
}

impl From<std::io::Error> for SidecarError {
    fn from(e: std::io::Error) -> Self { SidecarError::Io(e) }
}

impl Sidecar {
    fn spawn(program: &str) -> std::io::Result<Self> {
        let mut child = Command::new(program)
            .args(["serve", "--stdio"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null()) // or a log file: never an undrained pipe
            .spawn()?;
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        Ok(Sidecar { child, stdin, stdout, next_id: 1 })
    }

    /// One request, answered in order (no other request in flight). A host that
    /// pipelines requests must match responses by "id" instead.
    fn request(&mut self, method: &str, params: Value) -> Result<Value, SidecarError> {
        let id = self.next_id;
        self.next_id += 1;
        let line = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        writeln!(self.stdin, "{line}")?;
        self.stdin.flush()?;
        loop {
            let mut buf = String::new(); // grows as needed: result lines can be large
            if self.stdout.read_line(&mut buf)? == 0 {
                return Err(SidecarError::Closed);
            }
            let msg: Value = serde_json::from_str(&buf).map_err(|e| SidecarError::Io(e.into()))?;
            if msg.get("id") != Some(&json!(id)) {
                continue; // a notification ($/progress) or an unrelated response
            }
            if let Some(err) = msg.get("error") {
                return Err(SidecarError::Rpc {
                    code: err["code"].as_i64().unwrap_or(0),
                    slug: err["data"]["code"].as_str().unwrap_or("").to_owned(),
                    message: err["message"].as_str().unwrap_or("").to_owned(),
                });
            }
            return Ok(msg["result"].clone());
        }
    }

    fn close(mut self) -> std::io::Result<std::process::ExitStatus> {
        let _ = self.request("shutdown", json!({}));
        writeln!(self.stdin, "{}", json!({"jsonrpc": "2.0", "method": "exit"}))?;
        drop(self.stdin);
        self.child.wait()
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let (program, file) = (&args[1], &args[2]);
    let mut sidecar = Sidecar::spawn(program)?;
    let info = sidecar
        .request("initialize", json!({"protocol_version": "1.0",
            "client_info": {"name": "example-rust-host", "version": "0.1"}}))
        .map_err(|e| format!("{e:?}"))?;
    println!("pydiggs {} protocol {}", info["pydiggs_version"], info["protocol_version"]);
    let content = base64::engine::general_purpose::STANDARD.encode(std::fs::read(file)?);
    let doc = sidecar
        .request("validate", json!({"content_base64": content, "name": file}))
        .map_err(|e| format!("{e:?}"))?;
    for entry in doc["files"].as_array().unwrap() {
        // Untrusted text: escape before rendering in any UI.
        println!("{} {} {} findings", entry["path"], entry["verdict"],
                 entry["findings"].as_array().map_or(0, |f| f.len()));
    }
    match sidecar.request("validate", json!({"path": "relative.xml"})) {
        Err(SidecarError::Rpc { slug, code, .. }) => println!("refused: {slug} ({code})"),
        other => println!("unexpected: {other:?}"),
    }
    println!("exit code: {:?}", sidecar.close()?.code());
    Ok(())
}
```

In a Tauri application, declare the frozen sidecar as an external binary
(`bundle.externalBin`, named with the `-$TARGET_TRIPLE` suffix) and spawn it through the
shell plugin's sidecar API, or with `std::process::Command` as above.

### C# (.NET 8)

```csharp
// pydiggs sidecar client for .NET: System.Diagnostics.Process + System.Text.Json.
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;

sealed class SidecarException(int code, string slug, string message) : Exception(message)
{
    public int Code { get; } = code;
    public string Slug { get; } = slug; // error.data.code: switch on this
}

sealed class Sidecar : IAsyncDisposable
{
    private readonly Process _proc;
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonNode?>> _pending = new();
    private long _nextId;

    public Sidecar(string program)
    {
        var info = new ProcessStartInfo(program, "serve --stdio")
        {
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true, // no console window on Windows
        };
        _proc = Process.Start(info)!;
        _proc.ErrorDataReceived += (_, e) => { if (e.Data is not null) Console.Error.WriteLine(e.Data); };
        _proc.BeginErrorReadLine(); // always drain stderr
        _ = Task.Run(ReadLoop);
    }

    private async Task ReadLoop()
    {
        // ReadLineAsync has no fixed line limit: multi-megabyte results are fine.
        while (await _proc.StandardOutput.ReadLineAsync() is { } line)
        {
            var msg = JsonNode.Parse(line)!.AsObject();
            if (!msg.TryGetPropertyValue("id", out var idNode) || idNode is null) continue; // notification
            if (!_pending.TryRemove(idNode.GetValue<long>(), out var tcs)) continue;
            if (msg["error"] is JsonObject err)
                tcs.SetException(new SidecarException(
                    err["code"]!.GetValue<int>(), err["data"]?["code"]?.GetValue<string>() ?? "",
                    err["message"]!.GetValue<string>()));
            else
                tcs.SetResult(msg["result"]);
        }
        foreach (var tcs in _pending.Values) tcs.TrySetException(new IOException("sidecar exited"));
    }

    public async Task<JsonNode?> Request(string method, object? parameters = null, TimeSpan? timeout = null)
    {
        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        var line = JsonSerializer.Serialize(new { jsonrpc = "2.0", id, method, @params = parameters ?? new { } });
        lock (_proc) { _proc.StandardInput.Write(line + "\n"); _proc.StandardInput.Flush(); }
        try
        {
            return await tcs.Task.WaitAsync(timeout ?? TimeSpan.FromMinutes(2));
        }
        catch (TimeoutException)
        {
            _proc.Kill(entireProcessTree: true); // the host owns hard limits; respawn for the next file
            throw;
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_proc.HasExited) return;
        await Request("shutdown");
        lock (_proc) { _proc.StandardInput.Write("{\"jsonrpc\":\"2.0\",\"method\":\"exit\"}\n"); _proc.StandardInput.Close(); }
        await _proc.WaitForExitAsync();
        Console.WriteLine($"exit code: {_proc.ExitCode}");
    }
}

static class Program
{
    static async Task Main(string[] args)
    {
        await using var sidecar = new Sidecar(args[0]);
        var info = await sidecar.Request("initialize", new
        {
            protocol_version = "1.0",
            client_info = new { name = "example-dotnet-host", version = "0.1" },
        });
        Console.WriteLine($"pydiggs {info!["pydiggs_version"]} protocol {info["protocol_version"]}");
        var content = Convert.ToBase64String(await File.ReadAllBytesAsync(args[1]));
        var doc = await sidecar.Request("validate", new { content_base64 = content, name = Path.GetFileName(args[1]) });
        foreach (var entry in doc!["files"]!.AsArray())
            // Untrusted text: HTML-encode or otherwise escape before display.
            Console.WriteLine($"{entry!["path"]} {entry["verdict"]} {entry["findings"]!.AsArray().Count} findings");
        try { await sidecar.Request("validate", new { path = "relative.xml" }); }
        catch (SidecarException e) { Console.WriteLine($"refused: {e.Slug} ({e.Code})"); }
    }
}
```

### Other stacks

- **Qt (C++)**: `QProcess` with `setProcessChannelMode(QProcess::SeparateChannels)`;
  read stdout on `readyReadStandardOutput` with `canReadLine()`/`readLine()` in a loop,
  drain `readyReadStandardError`, and parse with `QJsonDocument::fromJson`. Use
  `QProcess::kill()` for hard timeouts.
- **Java / Kotlin**: `ProcessBuilder` with `redirectError(ProcessBuilder.Redirect.DISCARD)`
  or a file; read `process.inputReader(StandardCharsets.UTF_8)` line by line on a
  dedicated thread and complete `CompletableFuture`s by id (Jackson or `jakarta.json`).
  `process.destroyForcibly()` for hard timeouts.
- **Swift (macOS)**: `Foundation.Process` with `Pipe`s; read
  `standardOutput.fileHandleForReading.bytes.lines` (async) and decode each line with
  `JSONSerialization` or `Codable`; send a nil device or a drained pipe as
  `standardError`. `process.terminate()` sends SIGTERM (status 0); use `kill(pid, SIGKILL)`
  for a stuck validation.
