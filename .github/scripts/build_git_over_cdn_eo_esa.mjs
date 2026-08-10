#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

import yazl from "yazl";

const env = process.env;
// Cloudflare Worker 单文件上限 25MB，24MB 留出余量
const MAX_PACK_BYTES = 24 * 1024 * 1024;
const MAIN_SITE_URL = "https://alas.nanoda.work/";
const MAIN_SITE_HOST = "alas.nanoda.work";
const CDN_SITE_URLS = [
  "https://ap-update-cdn-cloudflare.3463343.xyz/",
  "https://ap-update-cdn-cloudflare-a3.haiteluo.com/",
  "https://ap-update-cdn-cloudflare-a1.3463343.xyz/",
  "https://ap-update-cdn-cloudflare.nanoda.work/",
  "https://ap-update-cdn-cloudflare-a2.3463343.xyz/",
  "https://ap-update-cdn-cloudflare-a1.haiteluo.com/",
  "https://ap-update-cdn-cloudflare-a2.haiteluo.com/",
  "https://ap-update-cdn-cloudflare-a4.haiteluo.com/",
  "https://ap-update-cdn-cloudflare-a3.3463343.xyz/",
  "https://ap.update.cdn.cloudflare.3463343.xyz/",
];
const DEFAULT_SITE_URL = CDN_SITE_URLS[0];
const SEO_TITLE = "AzurPilot 更新 CDN - 碧蓝航线自动化更新镜像";
const SEO_DESCRIPTION = "AzurPilot 更新 CDN 提供 Git over CDN 静态更新文件、latest.json、更新包状态与最近提交信息，主站为 https://alas.nanoda.work/。";

function printHelp() {
  console.log(`构建 EO/ESA/Pages 可用的 git-over-cdn 静态更新文件。

用法：
  node .github/scripts/build_git_over_cdn_eo_esa.mjs [options]

选项：
  --branch <name>      构建分支，默认 GOC_BRANCH/平台分支变量/master
  --ref <ref>          构建提交或引用，优先级高于 --branch
  --history <number>   生成多少个旧提交的更新包，默认 15
  --output <path>      输出目录，默认 dist/git-over-cdn
  --site-url <url>     canonical/sitemap 默认站点 URL，默认 ${DEFAULT_SITE_URL}
  --remote <name>      拉取历史时使用的 remote，默认 origin
  --no-fetch           跳过 git fetch
  --fetch-full         浅克隆时执行 git fetch --unshallow
  --help               显示帮助

环境变量：
  GOC_BRANCH, GOC_REF, GOC_HISTORY, GOC_OUTPUT, GOC_REMOTE,
  GOC_SITE_URL, GOC_MIRROR_URLS, GOC_FETCH=0, GOC_FETCH_FULL=1
`);
}

function envFirst(...names) {
  for (const name of names) {
    if (env[name]) {
      return env[name];
    }
  }
  return "";
}

function normalizeSiteUrl(value) {
  const raw = String(value || DEFAULT_SITE_URL).trim() || DEFAULT_SITE_URL;
  const withProtocol = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`;
  const url = new URL(withProtocol);
  if (!url.pathname.endsWith("/")) {
    url.pathname = `${url.pathname}/`;
  }
  url.search = "";
  url.hash = "";
  return url.toString();
}

function parseUrlList(value) {
  return String(value || "")
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueUrls(values) {
  const urls = [];
  const seen = new Set();
  for (const value of values) {
    const url = normalizeSiteUrl(value);
    if (seen.has(url)) {
      continue;
    }
    seen.add(url);
    urls.push(url);
  }
  return urls;
}

function resolveAssetUrl(siteUrl, filename) {
  return new URL(filename, siteUrl).toString();
}

function parseArgs(argv) {
  const options = {
    branch: envFirst("GOC_BRANCH", "CF_PAGES_BRANCH", "BRANCH", "GITHUB_REF_NAME") || "master",
    ref: envFirst("GOC_REF", "CF_PAGES_COMMIT_SHA", "COMMIT_SHA", "GITHUB_SHA"),
    history: env.GOC_HISTORY || "15",
    output: env.GOC_OUTPUT || "dist/git-over-cdn",
    siteUrl: normalizeSiteUrl(env.GOC_SITE_URL || DEFAULT_SITE_URL),
    remote: env.GOC_REMOTE || "origin",
    fetch: env.GOC_FETCH !== "0",
    fetchFull: env.GOC_FETCH_FULL === "1",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case "--branch":
        options.branch = requireValue(argv, ++i, arg);
        break;
      case "--ref":
        options.ref = requireValue(argv, ++i, arg);
        break;
      case "--history":
        options.history = requireValue(argv, ++i, arg);
        break;
      case "--output":
        options.output = requireValue(argv, ++i, arg);
        break;
      case "--site-url":
        options.siteUrl = normalizeSiteUrl(requireValue(argv, ++i, arg));
        break;
      case "--remote":
        options.remote = requireValue(argv, ++i, arg);
        break;
      case "--no-fetch":
        options.fetch = false;
        break;
      case "--fetch-full":
        options.fetchFull = true;
        break;
      case "--help":
      case "-h":
        printHelp();
        process.exit(0);
        break;
      default:
        throw new Error(`未知参数：${arg}`);
    }
  }

  options.history = Number(options.history);
  if (!Number.isInteger(options.history) || options.history < 1) {
    throw new Error(`--history 必须是正整数：${options.history}`);
  }
  options.mirrorUrls = uniqueUrls([
    options.siteUrl,
    ...CDN_SITE_URLS,
    ...parseUrlList(env.GOC_MIRROR_URLS),
  ]);

  return options;
}

function requireValue(argv, index, option) {
  if (index >= argv.length || argv[index].startsWith("--")) {
    throw new Error(`${option} 需要参数值`);
  }
  return argv[index];
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    input: options.input,
    encoding: options.encoding ?? "utf8",
    maxBuffer: options.maxBuffer ?? 64 * 1024 * 1024,
    stdio: options.stdio ?? "pipe",
  });

  if (result.error) {
    if (options.allowFailure) {
      return "";
    }
    throw result.error;
  }

  if (result.status !== 0) {
    if (options.allowFailure) {
      return "";
    }
    const stdout = result.stdout ? String(result.stdout).trim() : "";
    const stderr = result.stderr ? String(result.stderr).trim() : "";
    throw new Error(
      [`${command} ${args.join(" ")} 执行失败，退出码 ${result.status}`, stdout, stderr]
        .filter(Boolean)
        .join("\n"),
    );
  }

  return String(result.stdout ?? "").trim();
}

function runGit(args, cwd, options = {}) {
  return run("git", args, { cwd, ...options });
}

function gitOk(args, cwd) {
  const result = spawnSync("git", args, {
    cwd,
    stdio: "ignore",
  });
  return !result.error && result.status === 0;
}

function resolveRepoRoot() {
  return runGit(["rev-parse", "--show-toplevel"], process.cwd());
}

function maybeFetchHistory(options, repoRoot) {
  if (!options.fetch || !gitOk(["remote", "get-url", options.remote], repoRoot)) {
    return;
  }

  const fetchDepth = String(options.history + 5);
  const isShallow = runGit(
    ["rev-parse", "--is-shallow-repository"],
    repoRoot,
    { allowFailure: true },
  ) === "true";

  if (!isShallow) {
    runGit(["fetch", "--no-tags", options.remote, options.branch], repoRoot, { allowFailure: true });
    return;
  }

  if (options.fetchFull) {
    if (gitOk(["fetch", "--no-tags", "--unshallow", options.remote, options.branch], repoRoot)) {
      return;
    }
  } else if (gitOk(["fetch", "--no-tags", "--deepen", fetchDepth, options.remote, options.branch], repoRoot)) {
    return;
  }

  runGit(["fetch", "--no-tags", "--depth", fetchDepth, options.remote, options.branch], repoRoot);
}

function resolveBuildRef(options, repoRoot) {
  const candidates = [
    options.ref,
    options.branch,
    `refs/remotes/${options.remote}/${options.branch}`,
    "FETCH_HEAD",
    "HEAD",
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (gitOk(["rev-parse", "--verify", "--quiet", `${candidate}^{commit}`], repoRoot)) {
      return candidate;
    }
  }

  throw new Error("无法解析构建引用");
}

async function runToFile(command, args, input, outputFile, cwd) {
  await fs.promises.mkdir(path.dirname(outputFile), { recursive: true });

  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const output = fs.createWriteStream(outputFile);
    let stderr = "";
    let childCode = null;
    let outputFinished = false;
    let settled = false;

    function fail(error) {
      if (settled) {
        return;
      }
      settled = true;
      reject(error);
    }

    function maybeDone() {
      if (settled || childCode === null || !outputFinished) {
        return;
      }
      settled = true;
      if (childCode === 0) {
        resolve();
      } else {
        reject(new Error(`${command} ${args.join(" ")} 执行失败，退出码 ${childCode}\n${stderr.trim()}`));
      }
    }

    child.on("error", fail);
    child.on("close", (code) => {
      childCode = code;
      maybeDone();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    output.on("error", fail);
    output.on("finish", () => {
      outputFinished = true;
      maybeDone();
    });

    child.stdout.pipe(output);
    child.stdin.end(input);
  });
}

function zipFiles(zipPath, files) {
  return new Promise((resolve, reject) => {
    const zipFile = new yazl.ZipFile();
    const output = fs.createWriteStream(zipPath);

    output.on("close", resolve);
    output.on("error", reject);
    zipFile.outputStream.on("error", reject);
    zipFile.outputStream.pipe(output);

    for (const file of files) {
      zipFile.addFile(file.path, file.name);
    }
    zipFile.end();
  });
}

async function buildPack(latest, old, outputDir, repoRoot) {
  await fs.promises.mkdir(outputDir, { recursive: true });

  const packName = `pack-${latest}.pack`;
  const idxName = `pack-${latest}.idx`;
  const packPath = path.join(outputDir, packName);
  const idxPath = path.join(outputDir, idxName);
  const zipPath = path.join(outputDir, `${old}.zip`);
  const revs = Buffer.from(`${latest}\n^${old}\n`, "ascii");

  await runToFile("git", ["pack-objects", "--revs", "--stdout"], revs, packPath, repoRoot);
  runGit(["index-pack", "-o", idxPath, packPath], repoRoot);
  await zipFiles(zipPath, [
    { path: packPath, name: packName },
    { path: idxPath, name: idxName },
  ]);

  const size = fs.statSync(zipPath).size;
  if (size > MAX_PACK_BYTES) {
    // 超出 Worker 单文件存储上限，生成也无法上传，直接清理
    for (const file of [zipPath, packPath, idxPath]) {
      fs.rmSync(file, { force: true });
    }
    console.log(`  skip ${old}.zip (${fmtBytes(size)} > ${fmtBytes(MAX_PACK_BYTES)})`);
    return null;
  }

  return zipPath;
}

function fmtBytes(bytes) {
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (bytes < 1024) {
      return `${bytes.toFixed(1)} ${unit}`;
    }
    bytes /= 1024;
  }
  return `${bytes.toFixed(1)} TB`;
}

function cleanupPackArtifacts(outputDir) {
  if (!fs.existsSync(outputDir)) {
    return;
  }

  for (const name of fs.readdirSync(outputDir)) {
    if (/^pack-.*\.(pack|idx|rev)$/.test(name)) {
      fs.rmSync(path.join(outputDir, name), { force: true });
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function escapeScriptJson(value) {
  return String(value).replaceAll("<", "\\u003c");
}

function shortCommit(commit) {
  return commit.slice(0, 8);
}

function getCommitInfo(commit, repoRoot) {
  const output = runGit(["show", "-s", "--format=%ct%x00%an%x00%s", commit], repoRoot);
  const [committedAtSecondsText, authorName = "", subject = ""] = output.split("\0");
  const committedAtSeconds = Number(committedAtSecondsText);
  const committedAtTimestamp = committedAtSeconds * 1000;
  if (!Number.isSafeInteger(committedAtTimestamp)) {
    throw new Error(`无法读取提交时间：${commit}`);
  }

  return {
    commit,
    shortCommit: shortCommit(commit),
    authorName,
    subject,
    committedAt: new Date(committedAtTimestamp).toISOString(),
    committedAtTimestamp,
  };
}

function formatDuration(milliseconds) {
  const sign = milliseconds < 0 ? "-" : "";
  let rest = Math.abs(Math.trunc(milliseconds));
  const days = Math.floor(rest / 86400000);
  rest %= 86400000;
  const hours = Math.floor(rest / 3600000);
  rest %= 3600000;
  const minutes = Math.floor(rest / 60000);
  rest %= 60000;
  const seconds = Math.floor(rest / 1000);
  const millis = rest % 1000;

  return `${sign}${days}天 ${hours}时 ${minutes}分 ${seconds}秒 ${millis}毫秒`;
}

function writeSitemapXml(outputDir, mirrorUrls, generatedAt) {
  const urlEntries = mirrorUrls.map((siteUrl) => `  <url>
    <loc>${escapeXml(siteUrl)}</loc>
    <lastmod>${escapeXml(generatedAt)}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>0.7</priority>
  </url>`).join("\n");
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urlEntries}
</urlset>
`;

  fs.writeFileSync(path.join(outputDir, "sitemap.xml"), xml, "utf8");
}

function writeRobotsTxt(outputDir, mirrorUrls) {
  const sitemapLines = mirrorUrls.map((siteUrl) => `Sitemap: ${resolveAssetUrl(siteUrl, "sitemap.xml")}`);
  const text = [
    "User-agent: *",
    "Allow: /",
    "",
    ...sitemapLines,
    "",
  ].join("\n");

  fs.writeFileSync(path.join(outputDir, "robots.txt"), text, "utf8");
}

function writeIndexHtml(outputDir, options, latest, oldCommits, commitInfos, generatedAtTimestamp) {
  const latestCommitInfo = commitInfos[0];
  if (!latestCommitInfo) {
    throw new Error("无法读取最新提交信息");
  }

  const generatedAt = new Date(generatedAtTimestamp).toISOString();
  const commitAge = formatDuration(generatedAtTimestamp - latestCommitInfo.committedAtTimestamp);
  const structuredData = escapeScriptJson(JSON.stringify({
    "@context": "https://schema.org",
    "@type": "WebPage",
    name: SEO_TITLE,
    description: SEO_DESCRIPTION,
    url: options.siteUrl,
    isPartOf: {
      "@type": "WebSite",
      name: "AzurPilot",
      url: MAIN_SITE_URL,
    },
    about: "AzurPilot Git over CDN 更新镜像",
    relatedLink: [MAIN_SITE_URL, ...options.mirrorUrls],
    sameAs: options.mirrorUrls,
    dateModified: generatedAt,
  }, null, 2));
  const builtPacks = oldCommits.filter((commit) => fs.existsSync(path.join(outputDir, latest, `${commit}.zip`)));
  const packRows = builtPacks.map((commit) => {
    const filename = `${latest}/${commit}.zip`;
    return `
          <tr>
            <td><code>${escapeHtml(shortCommit(commit))}</code></td>
            <td><a href="${escapeHtml(filename)}">${escapeHtml(filename)}</a></td>
          </tr>`;
  }).join("");
  const generatedPackCount = builtPacks.length;
  const skippedPackCount = oldCommits.length - generatedPackCount;
  const commitRows = commitInfos.map((info, index) => `
          <tr>
            <td>${index === 0 ? "最新" : `前 ${index} 次`}</td>
            <td><code title="${escapeHtml(info.commit)}">${escapeHtml(info.shortCommit)}</code></td>
            <td>
              <time class="local-time" datetime="${escapeHtml(info.committedAt)}">${escapeHtml(info.committedAt)} UTC</time>
            </td>
            <td><code>${info.committedAtTimestamp}</code></td>
            <td>${escapeHtml(info.authorName)}</td>
            <td>${escapeHtml(info.subject)}</td>
          </tr>`).join("");
  const mirrorRows = options.mirrorUrls.map((siteUrl, index) => {
    const url = new URL(siteUrl);
    const latestJsonUrl = resolveAssetUrl(siteUrl, "latest.json");
    const sitemapUrl = resolveAssetUrl(siteUrl, "sitemap.xml");
    return `
          <tr>
            <td>${index === 0 ? "默认" : index + 1}</td>
            <td><a href="${escapeHtml(siteUrl)}">${escapeHtml(url.host)}</a></td>
            <td><a href="${escapeHtml(latestJsonUrl)}">latest.json</a></td>
            <td><a href="${escapeHtml(sitemapUrl)}">sitemap.xml</a></td>
          </tr>`;
  }).join("");
  const alternateLinks = options.mirrorUrls
    .filter((siteUrl) => siteUrl !== options.siteUrl)
    .map((siteUrl) => `<link rel="alternate" href="${escapeHtml(siteUrl)}">`)
    .join("\n  ");
  const seeAlsoMeta = [MAIN_SITE_URL, ...options.mirrorUrls.filter((siteUrl) => siteUrl !== options.siteUrl)]
    .map((siteUrl) => `<meta property="og:see_also" content="${escapeHtml(siteUrl)}">`)
    .join("\n  ");

  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(SEO_TITLE)}</title>
  <meta name="description" content="${escapeHtml(SEO_DESCRIPTION)}">
  <meta name="robots" content="index,follow">
  <link rel="canonical" href="${escapeHtml(options.siteUrl)}">
  <link rel="home" href="${escapeHtml(MAIN_SITE_URL)}">
  <link rel="sitemap" type="application/xml" href="${escapeHtml(resolveAssetUrl(options.siteUrl, "sitemap.xml"))}">
  ${alternateLinks}
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="AzurPilot">
  <meta property="og:title" content="${escapeHtml(SEO_TITLE)}">
  <meta property="og:description" content="${escapeHtml(SEO_DESCRIPTION)}">
  <meta property="og:url" content="${escapeHtml(options.siteUrl)}">
  ${seeAlsoMeta}
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="${escapeHtml(SEO_TITLE)}">
  <meta name="twitter:description" content="${escapeHtml(SEO_DESCRIPTION)}">
  <script type="application/ld+json">${structuredData}</script>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f8fb;
      --fg: #172033;
      --muted: #667085;
      --line: #d8dee8;
      --panel: #ffffff;
      --accent: #246bfe;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #121722;
        --fg: #eef3ff;
        --muted: #aab4c5;
        --line: #2a3344;
        --panel: #181f2d;
        --accent: #8ab4ff;
      }
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--fg);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }
    main {
      width: min(920px, calc(100% - 32px));
      margin: 0 auto;
      padding: 48px 0;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 28px;
      font-weight: 700;
    }
    p {
      margin: 0;
      color: var(--muted);
    }
    section {
      margin-top: 28px;
      padding-top: 24px;
      border-top: 1px solid var(--line);
    }
    dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 12px 18px;
      margin: 0;
    }
    dt {
      color: var(--muted);
    }
    dd {
      margin: 0;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    code {
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 0.95em;
    }
    a {
      color: var(--accent);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      background: var(--panel);
      border: 1px solid var(--line);
    }
    th,
    td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    tr:last-child td {
      border-bottom: 0;
    }
    th {
      color: var(--muted);
      font-weight: 600;
    }
    .empty {
      padding: 14px 0;
    }
  </style>
</head>
<body>
  <main>
    <h1>AzurPilot 更新 CDN</h1>
    <p>此页面由构建脚本自动生成，用于确认静态更新文件已发布。项目主站：<a href="${escapeHtml(MAIN_SITE_URL)}">${escapeHtml(MAIN_SITE_HOST)}</a></p>

    <section>
      <dl>
        <dt>最新版本</dt>
        <dd><code>${escapeHtml(latest)}</code></dd>
        <dt>构建分支</dt>
        <dd><code>${escapeHtml(options.branch)}</code></dd>
        <dt>项目主站</dt>
        <dd><a href="${escapeHtml(MAIN_SITE_URL)}">${escapeHtml(MAIN_SITE_URL)}</a></dd>
        <dt>CDN 首页</dt>
        <dd><a href="${escapeHtml(options.siteUrl)}">${escapeHtml(options.siteUrl)}</a></dd>
        <dt>更新包数量</dt>
        <dd>${generatedPackCount}${skippedPackCount ? `（跳过 ${skippedPackCount} 个超过 ${fmtBytes(MAX_PACK_BYTES)} 的）` : ""}</dd>
        <dt>生成时间</dt>
        <dd><time datetime="${escapeHtml(generatedAt)}">${escapeHtml(generatedAt)}</time></dd>
        <dt>版本接口</dt>
        <dd><a href="latest.json">latest.json</a></dd>
        <dt>站点地图</dt>
        <dd><a href="sitemap.xml">sitemap.xml</a></dd>
      </dl>
    </section>

    <section>
      <h2>CDN 镜像</h2>
      <table>
        <thead>
          <tr>
            <th>序号</th>
            <th>首页</th>
            <th>版本接口</th>
            <th>站点地图</th>
          </tr>
        </thead>
        <tbody>${mirrorRows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>更新包</h2>
      ${oldCommits.length ? `<table>
        <thead>
          <tr>
            <th>本地版本</th>
            <th>下载路径</th>
          </tr>
        </thead>
        <tbody>${packRows}
        </tbody>
      </table>` : '<p class="empty">当前没有生成旧版本更新包。</p>'}
    </section>
  </main>
</body>
</html>
`;

  fs.writeFileSync(path.join(outputDir, "index.html"), html, "utf8");
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const repoRoot = resolveRepoRoot();
  maybeFetchHistory(options, repoRoot);

  const buildRef = resolveBuildRef(options, repoRoot);
  const latest = runGit(["rev-parse", buildRef], repoRoot);
  const commits = runGit(
    ["rev-list", "--first-parent", `--max-count=${options.history + 1}`, latest],
    repoRoot,
  ).split(/\r?\n/).filter(Boolean);
  const oldCommits = commits.filter((commit) => commit !== latest);
  const outputDir = path.resolve(repoRoot, options.output);

  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(
    path.join(outputDir, "latest.json"),
    `${JSON.stringify({ commit: latest }, null, 2)}\n`,
    "utf8",
  );

  const latestDir = path.join(outputDir, latest);
  let skipped = 0;
  for (const old of oldCommits) {
    if (await buildPack(latest, old, latestDir, repoRoot) === null) {
      skipped += 1;
    }
  }
  cleanupPackArtifacts(latestDir);
  const generatedAtTimestamp = Date.now();
  const generatedAt = new Date(generatedAtTimestamp).toISOString();
  writeIndexHtml(outputDir, options, latest, oldCommits, commitInfos, generatedAtTimestamp);
  writeSitemapXml(outputDir, options.mirrorUrls, generatedAt);
  writeRobotsTxt(outputDir, options.mirrorUrls);

  console.log("Build git-over-cdn files");
  console.log(`  branch : ${options.branch}`);
  console.log(`  ref    : ${latest}`);
  console.log(`  history: ${options.history}`);
  console.log(`  output : ${path.relative(repoRoot, outputDir).replaceAll(path.sep, "/")}`);
  console.log(`  site   : ${options.siteUrl}`);
  console.log(`  mirrors: ${options.mirrorUrls.length}`);
  console.log(`Generated index.html, robots.txt, sitemap.xml, latest.json and ${oldCommits.length - skipped} update pack(s)`);
  if (skipped > 0) {
    console.log(`Skipped ${skipped} update pack(s) larger than ${fmtBytes(MAX_PACK_BYTES)} (Cloudflare Worker file limit)`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
