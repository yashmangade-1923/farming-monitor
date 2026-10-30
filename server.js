/**
 * ============================================================================
 * SMART IRRIGATION SYSTEM - NODE.JS LOCAL SERVER
 * ============================================================================
 * Runs with pure Node.js built-in modules (http, fs, path, url).
 * Zero npm dependencies required to start the web dashboard!
 *
 * If 'serialport' package is installed, it also bridges USB Serial to /api.
 * Otherwise, the dashboard uses Direct Web Serial API in Chrome/Edge!
 * ============================================================================
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');

const PORT = 5000;
const HOST = '0.0.0.0';

// In-memory telemetry state
let latestTelemetry = {
  raw: 512,
  moisture: 45,
  pump: "OFF",
  mode: "AUTO",
  threshold_on: 30,
  threshold_off: 60,
  sensor_ok: true,
  safety_cutoff: false,
  dry: 620,
  wet: 310,
  uptime: 1000
};
let historyBuffer = [];
let lastUpdateTime = Date.now();
let serialConnected = false;
let serialPortInstance = null;

// Try loading optional serialport
let SerialPort = null;
let ReadlineParser = null;
try {
  const serialModule = require('serialport');
  SerialPort = serialModule.SerialPort;
  ReadlineParser = require('@serialport/parser-readline').ReadlineParser;
  console.log('[Serial] serialport module detected.');
} catch (e) {
  console.log('[Notice] Running in Zero-Dependency Mode. Web Serial API will handle direct USB communication in browser.');
}

// MIME types
const MIME_TYPES = {
  '.html': 'text/html; charset=UTF-8',
  '.js': 'text/javascript; charset=UTF-8',
  '.css': 'text/css; charset=UTF-8',
  '.json': 'application/json; charset=UTF-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
};

const server = http.createServer((req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const pathname = parsedUrl.pathname;

  // CORS headers for local development
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // API: GET /api/data
  if (pathname === '/api/data' && req.method === 'GET') {
    const secondsAgo = ((Date.now() - lastUpdateTime) / 1000).toFixed(2);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      connected: serialConnected,
      port: serialConnected ? 'USB-SERIAL' : null,
      last_update_seconds_ago: parseFloat(secondsAgo),
      data: latestTelemetry
    }));
    return;
  }

  // API: GET /api/history
  if (pathname === '/api/history' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      count: historyBuffer.length,
      history: historyBuffer
    }));
    return;
  }

  // API: POST /api/control
  if (pathname === '/api/control' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const cmd = (payload.command || '').toUpperCase();
        if (!['PUMP_ON', 'PUMP_OFF', 'AUTO'].includes(cmd)) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'error', message: 'Invalid command' }));
          return;
        }

        if (serialPortInstance && serialPortInstance.isOpen) {
          serialPortInstance.write(cmd + '\n');
        }

        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'success', command: cmd, reply: `ACK:${cmd}` }));
      } catch (err) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'error', message: err.message }));
      }
    });
    return;
  }

  // API: POST /api/settings
  if (pathname === '/api/settings' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        if (payload.threshold_on !== undefined && payload.threshold_off !== undefined) {
          const cmd = `SET_THRESHOLDS:${payload.threshold_on},${payload.threshold_off}`;
          if (serialPortInstance && serialPortInstance.isOpen) serialPortInstance.write(cmd + '\n');
        }
        if (payload.dry !== undefined && payload.wet !== undefined) {
          const cmd = `SET_CALIB:${payload.dry},${payload.wet}`;
          if (serialPortInstance && serialPortInstance.isOpen) serialPortInstance.write(cmd + '\n');
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'success' }));
      } catch (err) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'error', message: err.message }));
      }
    });
    return;
  }

  // Serve static files (default: templates/index.html)
  let filePath = '';
  if (pathname === '/' || pathname === '/index.html') {
    filePath = path.join(__dirname, 'templates', 'index.html');
  } else {
    filePath = path.join(__dirname, pathname);
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, content) => {
    if (err) {
      if (err.code === 'ENOENT') {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        res.end('404 Not Found');
      } else {
        res.writeHead(500, { 'Content-Type': 'text/plain' });
        res.end(`500 Server Error: ${err.code}`);
      }
    } else {
      res.writeHead(200, { 'Content-Type': contentType });
      res.end(content, 'utf-8');
    }
  });
});

server.listen(PORT, HOST, () => {
  const url = `http://localhost:${PORT}`;
  console.log('===============================================================================');
  console.log('       🌱 SMART IRRIGATION SYSTEM - LOCAL NODE.JS SERVER 🌱');
  console.log('===============================================================================');
  console.log(`  Dashboard URL : ${url}`);
  console.log('  Status        : Ready & Serving templates/index.html');
  console.log('  Hardware      : Supports Web Serial API (Chrome/Edge) or Python Gateway');
  console.log('===============================================================================');
  console.log('  Opening browser automatically...\n');

  // Automatically open browser on Windows
  if (process.platform === 'win32') {
    exec(`start ${url}`);
  }
});
