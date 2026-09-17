#!/usr/bin/env node
/**
 * 用无头 Chrome 把本地 HTML 渲染成 PDF（可选地加页码页脚）。
 *
 * 关于页眉页脚 —— 两条路，二选一，**不要同时开**：
 *
 *   1. 默认（不加 --header-footer）：由 CSS 自己用 `@page` 页边距框
 *      （`@top-right` / `@bottom-center` 配合 `counter(page)`）绘制。
 *      当前 Chrome 已经支持这套 CSS Paged Media 语法，样式可以完全交给设计稿控制，
 *      而且能用 `@page :first` 让封面不出现页眉页脚。**推荐走这条**。
 *   2. 加 --header-footer：由本脚本通过 Page.printToPDF 的 headerTemplate /
 *      footerTemplate 绘制。适合样式表里没有定义页边距框的文档。
 *      两条同时开会得到**重复的**页眉与页码。
 *
 * 为什么还保留第 2 条：更老的 Chromium 不支持 CSS 页边距框，那时只能靠它。
 *
 * Node 22 起内置了全局 WebSocket，因此不需要任何第三方依赖。
 *
 * 用法：
 *   node tools/render_pdf.js --html build/technical.html --out docs/TECHNICAL.pdf \
 *        [--running-title "…"] [--header-footer]
 */

'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// --------------------------------------------------------------------------- //
// 参数
// --------------------------------------------------------------------------- //

function parseArgs(argv) {
  const args = {
    html: null,
    out: null,
    chrome: null,
    runningTitle: '',
    scale: 1,
    timeout: 120000,
    printBackground: true,
    preferCssPageSize: true,
    headerFooter: false,
    outline: true,
    marginTop: 0.4,
    marginBottom: 0.4,
    marginLeft: 0,
    marginRight: 0,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => argv[++i];
    switch (key) {
      case '--html': args.html = next(); break;
      case '--out': args.out = next(); break;
      case '--chrome': args.chrome = next(); break;
      case '--running-title': args.runningTitle = next(); break;
      case '--scale': args.scale = Number(next()); break;
      case '--timeout': args.timeout = Number(next()); break;
      case '--header-footer': args.headerFooter = true; break;
      case '--no-outline': args.outline = false; break;
      case '--margin-top': args.marginTop = Number(next()); break;
      case '--margin-bottom': args.marginBottom = Number(next()); break;
      default: break;
    }
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (!args.html || !args.out) {
  console.error('用法：node tools/render_pdf.js --html <输入.html> --out <输出.pdf>');
  process.exit(2);
}

// --------------------------------------------------------------------------- //
// 找浏览器
// --------------------------------------------------------------------------- //

function findChrome() {
  if (args.chrome && fs.existsSync(args.chrome)) return args.chrome;
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  throw new Error('找不到 Chrome 或 Edge，可显式指定 --chrome <路径>');
}

// --------------------------------------------------------------------------- //
// 极简 CDP 客户端
// --------------------------------------------------------------------------- //

class Cdp {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (event) => reject(new Error(`WebSocket 出错：${event.message || event.type}`)));
      this.ws.addEventListener('message', (event) => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.id && this.pending.has(message.id)) {
          const { resolve: ok, reject: fail } = this.pending.get(message.id);
          this.pending.delete(message.id);
          if (message.error) fail(new Error(`${message.error.message}（${JSON.stringify(message.error.data || {})}）`));
          else ok(message.result);
          return;
        }
        if (message.method && this.handlers.has(message.method)) {
          for (const handler of this.handlers.get(message.method)) handler(message.params, message.sessionId);
        }
      });
    });
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }

  once(method, sessionId = undefined, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`等待 ${method} 超时`)), timeoutMs);
      const handler = (params, sid) => {
        if (sessionId && sid !== sessionId) return;
        clearTimeout(timer);
        const list = this.handlers.get(method) || [];
        this.handlers.set(method, list.filter((h) => h !== handler));
        resolve(params);
      };
      const list = this.handlers.get(method) || [];
      list.push(handler);
      this.handlers.set(method, list);
    });
  }

  close() {
    try { this.ws.close(); } catch { /* 忽略 */ }
  }
}

// --------------------------------------------------------------------------- //
// 页眉页脚模板（Chromium 只允许内联样式）
// --------------------------------------------------------------------------- //

function escapeHtml(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const FONT = "'Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif";

function footerTemplate(title) {
  const left = escapeHtml(title || '');
  return `
<div style="width:100%;padding:0 18mm;font-family:${FONT};font-size:8px;color:#8a8f98;
            display:flex;align-items:center;justify-content:space-between;">
  <span style="letter-spacing:.02em;">${left}</span>
  <span><span class="pageNumber"></span>&thinsp;/&thinsp;<span class="totalPages"></span></span>
</div>`;
}

function headerTemplate(title) {
  const right = escapeHtml(title || '');
  return `
<div style="width:100%;padding:0 18mm;font-family:${FONT};font-size:8px;color:#b3b8c0;
            display:flex;align-items:center;justify-content:flex-end;">
  <span>${right}</span>
</div>`;
}

// --------------------------------------------------------------------------- //
// 主流程
// --------------------------------------------------------------------------- //

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const chrome = findChrome();
  const htmlPath = path.resolve(args.html);
  if (!fs.existsSync(htmlPath)) throw new Error(`找不到输入文件：${htmlPath}`);
  const outPath = path.resolve(args.out);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chrome-pdf-'));
  const child = spawn(chrome, [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--hide-scrollbars',
    '--allow-file-access-from-files',
    `--user-data-dir=${userDataDir}`,
    '--remote-debugging-port=0',
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  // Chrome 会把 "DevTools listening on ws://..." 打到 stderr，用它拿到端口可以
  // 完全避免端口冲突。
  const wsUrl = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('等待 DevTools 端口超时')), 30000);
    let buffer = '';
    child.stderr.on('data', (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/ws:\/\/[^\s]+/);
      if (match) {
        clearTimeout(timer);
        resolve(match[0]);
      }
    });
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Chrome 提前退出，退出码 ${code}`));
    });
  });

  const cdp = new Cdp(wsUrl);
  await cdp.connect();

  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });

  await cdp.send('Page.enable', {}, sessionId);
  const loaded = cdp.once('Page.loadEventFired', sessionId, args.timeout);
  await cdp.send('Page.navigate', { url: `file:///${htmlPath.replace(/\\/g, '/')}` }, sessionId);
  await loaded;

  // 等字体与排版稳定（中文字体加载较慢，过早打印会得到错误的分页）
  await cdp.send('Runtime.evaluate', {
    expression: 'document.fonts ? document.fonts.ready.then(() => true) : true',
    awaitPromise: true,
  }, sessionId);
  await sleep(600);

  const title = args.runningTitle || '';
  const printOptions = {
    printBackground: args.printBackground,
    preferCSSPageSize: args.preferCssPageSize,
    displayHeaderFooter: args.headerFooter,
    marginTop: args.marginTop,
    marginBottom: args.marginBottom,
    marginLeft: args.marginLeft,
    marginRight: args.marginRight,
    scale: args.scale,
  };
  if (args.headerFooter) {
    printOptions.headerTemplate = headerTemplate(title);
    printOptions.footerTemplate = footerTemplate(title);
  }
  // 把 h1~h6 变成 PDF 书签。阅读器左侧的导航栏靠它，比目录页更好用。
  // 老版本 Chromium 不认识这个参数，会直接报错，因此失败时回退到不带书签再打一次。
  if (args.outline) printOptions.generateDocumentOutline = true;
  let data;
  try {
    ({ data } = await cdp.send('Page.printToPDF', printOptions, sessionId));
  } catch (error) {
    if (!args.outline) throw error;
    console.warn(`[render_pdf] 生成书签失败，改为不带书签重试：${error.message}`);
    delete printOptions.generateDocumentOutline;
    ({ data } = await cdp.send('Page.printToPDF', printOptions, sessionId));
  }

  fs.writeFileSync(outPath, Buffer.from(data, 'base64'));
  cdp.close();
  try { child.kill(); } catch { /* 忽略 */ }
  await sleep(200);
  try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch { /* 忽略 */ }

  const size = fs.statSync(outPath).size;
  console.log(`已生成 PDF：${outPath}（${(size / 1024).toFixed(1)} KB）`);
}

main().catch((error) => {
  console.error('[render_pdf] ' + error.message);
  process.exit(1);
});
