const http = require("http");
const fs = require("fs/promises");
const path = require("path");
const { spawn } = require("child_process");

const root = __dirname;
const host = "127.0.0.1";
const port = Number(process.env.PORT || 4177);
const noCacheHeaders = {
  "Cache-Control": "no-store, max-age=0",
  "Pragma": "no-cache",
  "Expires": "0"
};
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type"
};

function jsonHeaders(extra = {}) {
  return { "Content-Type": "application/json; charset=utf-8", ...noCacheHeaders, ...corsHeaders, ...extra };
}

function textHeaders(extra = {}) {
  return { "Content-Type": "text/plain; charset=utf-8", ...noCacheHeaders, ...corsHeaders, ...extra };
}

function safeFileName(name) {
  return String(name || "branch-builder-circuit.json").replace(/[^A-Za-z0-9._-]/g, "_");
}

function contentType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".json")) return "application/json; charset=utf-8";
  if (filePath.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  return "text/plain; charset=utf-8";
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", chunk => {
      body += chunk;
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(body || "{}"));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function runReduction(payload) {
  return runPythonJson("reduce_api.py", payload);
}

function runBlackBoxValidation(payload) {
  return runPythonJson("blackbox_validation_api.py", payload);
}

function runOptimizedElimination(payload) {
  return runPythonJson("optimized_elimination_api.py", payload);
}

function runPythonJson(scriptName, payload) {
  return new Promise((resolve, reject) => {
    const child = spawn("python", [path.join(root, scriptName)], {
      cwd: root,
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", chunk => {
      stdout += chunk;
    });
    child.stderr.on("data", chunk => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", code => {
      try {
        const data = JSON.parse(stdout || "{}");
        if (code !== 0 || data.ok === false) {
          reject(new Error(data.error || stderr || `Python ${scriptName} failed with code ${code}`));
          return;
        }
        resolve(data);
      } catch (error) {
        reject(new Error(stderr || error.message));
      }
    });

    child.stdin.end(JSON.stringify(payload));
  });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${host}:${port}`);

    if (req.method === "OPTIONS") {
      res.writeHead(204, { ...noCacheHeaders, ...corsHeaders });
      res.end();
      return;
    }

    if (req.method === "POST" && url.pathname === "/save-circuit") {
      try {
          const payload = await readJsonBody(req);
          const exportsDir = path.join(root, "exports");
          await fs.mkdir(exportsDir, { recursive: true });

          const fileName = safeFileName(payload.filename);
          const filePath = path.join(exportsDir, fileName);
          const data = String(payload.data || "");
          await fs.writeFile(filePath, data, "utf8");

          res.writeHead(200, jsonHeaders());
          res.end(JSON.stringify({
            ok: true,
            path: filePath,
            bytes: Buffer.byteLength(data, "utf8")
          }));
        } catch (error) {
          res.writeHead(500, jsonHeaders());
          res.end(JSON.stringify({ ok: false, error: String(error) }));
        }
      return;
    }

    if (req.method === "POST" && url.pathname === "/reduce-system") {
      try {
        const payload = await readJsonBody(req);
        const data = await runReduction(payload);
        res.writeHead(200, jsonHeaders());
        res.end(JSON.stringify(data));
      } catch (error) {
        res.writeHead(500, jsonHeaders());
        res.end(JSON.stringify({ ok: false, error: String(error.message || error) }));
      }
      return;
    }

    if (req.method === "POST" && url.pathname === "/validate-blackbox-observers") {
      try {
        const payload = await readJsonBody(req);
        const data = await runBlackBoxValidation(payload);
        res.writeHead(200, jsonHeaders());
        res.end(JSON.stringify(data));
      } catch (error) {
        res.writeHead(500, jsonHeaders());
        res.end(JSON.stringify({ ok: false, error: String(error.message || error) }));
      }
      return;
    }

    if (req.method === "POST" && (url.pathname === "/optimized-elimination" || url.pathname === "/multi-case-c-export")) {
      try {
        const payload = await readJsonBody(req);
        if (url.pathname === "/multi-case-c-export") payload.mode = "multi_case_c_export";
        const data = await runOptimizedElimination(payload);
        res.writeHead(200, jsonHeaders());
        res.end(JSON.stringify(data));
      } catch (error) {
        res.writeHead(500, jsonHeaders());
        res.end(JSON.stringify({ ok: false, error: String(error.message || error) }));
      }
      return;
    }

    if (req.method === "GET" && url.pathname === "/list-circuits") {
      const exportsDir = path.join(root, "exports");
      await fs.mkdir(exportsDir, { recursive: true });
      const entries = await fs.readdir(exportsDir, { withFileTypes: true });
      const files = [];
      for (const entry of entries) {
        if (!entry.isFile() || !entry.name.toLowerCase().endsWith(".json")) continue;
        const filePath = path.join(exportsDir, entry.name);
        const stat = await fs.stat(filePath);
        files.push({
          name: entry.name,
          size: stat.size,
          modified: stat.mtime.toISOString()
        });
      }
      files.sort((a, b) => b.modified.localeCompare(a.modified));
      res.writeHead(200, jsonHeaders());
      res.end(JSON.stringify({ files }));
      return;
    }

    if (req.method !== "GET") {
      res.writeHead(405, textHeaders());
      res.end("Method not allowed");
      return;
    }

    let pathname = decodeURIComponent(url.pathname);
    if (pathname === "/") pathname = "/index.html";

    const filePath = path.normalize(path.join(root, pathname));
    if (!filePath.startsWith(path.normalize(root))) {
      res.writeHead(403, textHeaders());
      res.end("Forbidden");
      return;
    }

    const data = await fs.readFile(filePath);
    res.writeHead(200, { "Content-Type": contentType(filePath), ...noCacheHeaders, ...corsHeaders });
    res.end(data);
  } catch {
    res.writeHead(404, textHeaders());
    res.end("Not found");
  }
});

server.listen(port, host, () => {
  console.log(`Branch Builder running at http://${host}:${port}/`);
});
