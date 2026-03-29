import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export class CairnPanel {
    public static currentPanel: CairnPanel | undefined;

    private readonly _panel: vscode.WebviewPanel;
    private readonly _extensionUri: vscode.Uri;
    private _sessionId: string;
    private _projectId: string;
    private _disposables: vscode.Disposable[] = [];
    public sessionCost: number = 0;

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor
            ? vscode.ViewColumn.Beside
            : vscode.ViewColumn.One;

        if (CairnPanel.currentPanel) {
            CairnPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'cairnAgent',
            'Cairn',
            column,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            }
        );

        CairnPanel.currentPanel = new CairnPanel(panel, extensionUri);
    }

    private _getApiConfig() {
        const config = vscode.workspace.getConfiguration('cairn');
        const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
        let apiKey = config.get<string>('apiKey', '');
        if (!apiKey) {
            try {
                const envPath = path.join('D:', 'claw', '.env');
                const envContent = fs.readFileSync(envPath, 'utf-8');
                const match = envContent.match(/^CLAW_API_KEY=(.+)$/m);
                if (match) { apiKey = match[1].trim(); }
            } catch { /* ignore */ }
        }
        return { apiUrl, apiKey };
    }

    private constructor(
        panel: vscode.WebviewPanel,
        extensionUri: vscode.Uri,
    ) {
        this._panel = panel;
        this._extensionUri = extensionUri;
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        const config = vscode.workspace.getConfiguration('cairn');
        this._projectId = config.get<string>('defaultProject', '') || 'claw';

        this._panel.webview.html = this._getHtml();

        // Notify panel when active editor changes
        vscode.window.onDidChangeActiveTextEditor(editor => {
            if (editor) {
                this._panel.webview.postMessage({
                    type: 'activeFileChanged',
                    filePath: editor.document.uri.fsPath,
                });
            }
        }, null, this._disposables);

        this._panel.webview.onDidReceiveMessage(
            async (message) => {
                switch (message.type) {
                    case 'sendMessage':
                        await this._handleSendStream(message);
                        break;
                    case 'approveTool':
                        await this._handleApproval(message, true);
                        break;
                    case 'rejectTool':
                        await this._handleApproval(message, false);
                        break;
                    case 'showDiff':
                        await this._showNativeDiff(message);
                        break;
                    case 'switchProject':
                        this._projectId = message.projectId;
                        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
                        this.sessionCost = 0;
                        const cfg = vscode.workspace.getConfiguration('cairn');
                        await cfg.update('defaultProject', message.projectId, vscode.ConfigurationTarget.Global);
                        this._panel.webview.postMessage({
                            type: 'init',
                            projectId: this._projectId,
                            sessionId: this._sessionId,
                        });
                        break;
                    case 'ready':
                        // Fetch projects list and send to panel
                        const projects = await this._fetchProjects();
                        this._panel.webview.postMessage({
                            type: 'init',
                            projectId: this._projectId,
                            sessionId: this._sessionId,
                            projects,
                        });
                        break;
                }
            },
            null,
            this._disposables,
        );

        this._panel.onDidDispose(
            () => this.dispose(),
            null,
            this._disposables,
        );
    }

    private async _fetchProjects(): Promise<Array<{ id: string; name: string }>> {
        const { apiUrl, apiKey } = this._getApiConfig();
        try {
            const res = await fetch(`${apiUrl}/projects`, {
                headers: apiKey ? { 'X-API-Key': apiKey } : {},
                signal: AbortSignal.timeout(5000),
            });
            if (!res.ok) { return []; }
            const data = await res.json() as { projects: Array<{ id: string; name: string; ready: boolean }> };
            return (data.projects || []).filter(p => p.ready).map(p => ({ id: p.id, name: p.name }));
        } catch {
            return [];
        }
    }

    public newSession() {
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        this.sessionCost = 0;
        this._panel.webview.postMessage({
            type: 'newSession',
            sessionId: this._sessionId,
        });
    }

    public switchProject(projectId: string) {
        this._projectId = projectId;
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        this.sessionCost = 0;
        this._panel.webview.postMessage({
            type: 'init',
            projectId: this._projectId,
            sessionId: this._sessionId,
        });
    }

    public reveal() {
        this._panel.reveal();
    }

    public addMention(mention: { type: string; value: string; display: string }) {
        this._panel.webview.postMessage({ type: 'addMention', mention });
    }

    /** SSE streaming chat — matches the web UI approach */
    private async _handleSendStream(message: {
        content: string;
        mentions?: Array<{ type: string; value: string; display: string }>;
        modelOverride?: string;
    }) {
        const { apiUrl, apiKey } = this._getApiConfig();

        const editor = vscode.window.activeTextEditor;
        const activeFile = editor?.document.uri.fsPath ?? null;
        const selection = editor?.selection;
        const selectedText = (selection && !selection.isEmpty)
            ? editor!.document.getText(selection)
            : null;

        const params = new URLSearchParams({
            project: this._projectId,
            session_id: this._sessionId,
            message: message.content,
        });
        if (message.mentions?.length) {
            params.set('mentions', JSON.stringify(message.mentions));
        }
        if (message.modelOverride) {
            params.set('model_override', message.modelOverride);
        }

        const url = `${apiUrl}/chat/stream?${params.toString()}`;

        try {
            const res = await fetch(url, {
                headers: {
                    'Accept': 'text/event-stream',
                    ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                },
            });

            if (!res.ok || !res.body) {
                this._panel.webview.postMessage({
                    type: 'error',
                    message: `API returned ${res.status}`,
                });
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) { break; }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) { continue; }
                    const payload = line.slice(6).trim();
                    if (!payload || payload === '[DONE]') { continue; }

                    try {
                        const event = JSON.parse(payload);
                        this._panel.webview.postMessage({
                            type: 'streamEvent',
                            event,
                        });

                        // Track cost
                        if (event.type === 'complete' && event.cost_usd) {
                            this.sessionCost += event.cost_usd;
                        }
                    } catch { /* skip malformed JSON */ }
                }
            }

            // Signal stream end
            this._panel.webview.postMessage({ type: 'streamEnd' });

        } catch (err) {
            this._panel.webview.postMessage({
                type: 'error',
                message: `Cannot reach Cairn API at ${apiUrl}. Is it running?`,
            });
        }
    }

    private async _handleApproval(message: {
        toolCallId: string;
        toolName: string;
        toolInput: Record<string, unknown>;
    }, approved: boolean) {
        const { apiUrl, apiKey } = this._getApiConfig();

        try {
            const res = await fetch(`${apiUrl}/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                },
                body: JSON.stringify({
                    content: '',
                    project_id: this._projectId,
                    session_id: this._sessionId,
                    channel: 'vscode',
                    tool_approval: {
                        tool_call_id: message.toolCallId,
                        tool_name: message.toolName,
                        input: message.toolInput,
                        approved,
                    },
                }),
            });

            const data = await res.json() as {
                content: string;
                pending_tool_call: Record<string, unknown> | null;
                model_used: string;
                cost_usd: number;
            };

            this._panel.webview.postMessage({
                type: 'agentResponse',
                content: data.content,
                pendingToolCall: data.pending_tool_call,
                modelUsed: data.model_used,
                costUsd: data.cost_usd,
            });

            if (data.cost_usd) {
                this.sessionCost += data.cost_usd;
            }
        } catch (err) {
            this._panel.webview.postMessage({
                type: 'error',
                message: `Approval failed: ${err}`,
            });
        }
    }

    /** Show proposed edit in VS Code's native diff editor */
    private async _showNativeDiff(message: {
        filePath: string;
        oldContent: string;
        newContent: string;
    }) {
        const tmpDir = os.tmpdir();
        const baseName = path.basename(message.filePath);

        const originalPath = path.join(tmpDir, `cairn-orig-${baseName}`);
        const modifiedPath = path.join(tmpDir, `cairn-mod-${baseName}`);

        fs.writeFileSync(originalPath, message.oldContent, 'utf-8');
        fs.writeFileSync(modifiedPath, message.newContent, 'utf-8');

        const originalUri = vscode.Uri.file(originalPath);
        const modifiedUri = vscode.Uri.file(modifiedPath);

        await vscode.commands.executeCommand(
            'vscode.diff',
            originalUri,
            modifiedUri,
            `Cairn: proposed changes to ${baseName}`
        );
    }

    private _getHtml(): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cairn</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:var(--vscode-font-family);
    font-size:var(--vscode-font-size);
    color:var(--vscode-foreground);
    background:var(--vscode-editor-background);
    height:100vh;display:flex;flex-direction:column;overflow:hidden
  }
  #header{
    padding:6px 12px;
    background:var(--vscode-panel-background);
    border-bottom:1px solid var(--vscode-panel-border);
    display:flex;align-items:center;gap:8px;
    font-size:11px;flex-shrink:0
  }
  #header-title{
    font-weight:bold;color:var(--vscode-textLink-foreground);
    letter-spacing:.5px;font-size:12px
  }
  #project-select{
    background:var(--vscode-dropdown-background);
    color:var(--vscode-dropdown-foreground);
    border:1px solid var(--vscode-dropdown-border);
    border-radius:3px;padding:2px 6px;font-size:11px;
    cursor:pointer
  }
  #model-badge{
    color:var(--vscode-descriptionForeground);
    margin-left:auto
  }
  #messages{
    flex:1;overflow-y:auto;padding:12px;
    display:flex;flex-direction:column;gap:10px
  }
  .msg{line-height:1.55;max-width:100%;word-break:break-word}
  .msg.user{
    background:var(--vscode-input-background);
    border-radius:6px;padding:8px 11px;
    border-left:2px solid var(--vscode-textLink-foreground)
  }
  .msg.assistant{padding:2px 0}
  .msg pre{
    background:var(--vscode-textBlockQuote-background);
    border-radius:4px;padding:8px;overflow-x:auto;
    font-family:var(--vscode-editor-font-family);
    font-size:var(--vscode-editor-font-size);margin:6px 0
  }
  .msg code{
    background:var(--vscode-textBlockQuote-background);
    border-radius:3px;padding:1px 4px;
    font-family:var(--vscode-editor-font-family)
  }
  .activity-log{
    font-size:11px;color:var(--vscode-descriptionForeground);
    margin:4px 0;padding:4px 8px;
    border-left:2px solid var(--vscode-panel-border)
  }
  .activity-log .activity-item{margin:2px 0}
  .tool-card{
    border:1px solid var(--vscode-editorWarning-foreground,#cca700);
    border-radius:6px;padding:10px 12px;margin:4px 0;
    background:var(--vscode-editor-background)
  }
  .tool-card .tool-header{
    display:flex;align-items:center;gap:6px;
    font-weight:bold;margin-bottom:4px;font-size:12px
  }
  .tool-card .tool-desc{
    color:var(--vscode-descriptionForeground);font-size:11px;margin-bottom:8px
  }
  .diff{
    font-family:var(--vscode-editor-font-family);
    font-size:11px;max-height:180px;overflow-y:auto;
    background:var(--vscode-editor-background);
    border:1px solid var(--vscode-panel-border);
    border-radius:3px;padding:6px;margin-bottom:8px;white-space:pre
  }
  .diff .add{color:#4ec9b0}.diff .del{color:#f44747}
  .diff .diff-file{color:var(--vscode-textLink-foreground);font-weight:bold}
  .diff .diff-hunk{color:var(--vscode-descriptionForeground)}
  .git-status-clean{color:#4ec9b0;padding:4px 0}
  .tool-btns{display:flex;gap:6px}
  button{
    padding:4px 12px;border-radius:3px;border:none;
    cursor:pointer;font-size:12px
  }
  .btn-ok{
    background:var(--vscode-button-background);
    color:var(--vscode-button-foreground)
  }
  .btn-ok:hover{background:var(--vscode-button-hoverBackground)}
  .btn-cancel{
    background:var(--vscode-button-secondaryBackground);
    color:var(--vscode-button-secondaryForeground)
  }
  .msg-footer{
    font-size:10px;color:var(--vscode-descriptionForeground);
    margin-top:4px;padding-top:4px;
    border-top:1px solid var(--vscode-panel-border)
  }
  #cost-row{
    padding:3px 12px;font-size:10px;
    color:var(--vscode-descriptionForeground);
    background:var(--vscode-panel-background);
    border-top:1px solid var(--vscode-panel-border);
    flex-shrink:0
  }
  #mentions-row{
    padding:4px 12px 0;background:var(--vscode-panel-background);
    display:flex;flex-wrap:wrap;gap:4px;flex-shrink:0;
    min-height:0
  }
  .mention-pill{
    display:inline-flex;align-items:center;gap:4px;
    background:var(--vscode-input-background);
    border:1px solid var(--vscode-panel-border);
    border-radius:4px;padding:1px 6px;font-size:11px;
    color:var(--vscode-foreground)
  }
  .mention-pill button{
    background:none;border:none;padding:0 0 0 2px;
    cursor:pointer;color:var(--vscode-descriptionForeground);
    font-size:11px;height:auto
  }
  #input-row{
    padding:8px 12px;
    border-top:1px solid var(--vscode-panel-border);
    background:var(--vscode-panel-background);
    display:flex;gap:8px;align-items:flex-end;flex-shrink:0
  }
  textarea{
    flex:1;background:var(--vscode-input-background);
    color:var(--vscode-input-foreground);
    border:1px solid var(--vscode-input-border,#555);
    border-radius:4px;padding:6px 8px;
    font-family:var(--vscode-font-family);
    font-size:var(--vscode-font-size);
    resize:none;min-height:34px;max-height:120px;line-height:1.4
  }
  textarea:focus{outline:1px solid var(--vscode-focusBorder)}
  #model-select{
    background:var(--vscode-dropdown-background);
    color:var(--vscode-dropdown-foreground);
    border:1px solid var(--vscode-dropdown-border);
    border-radius:3px;padding:2px 4px;font-size:11px;
    height:34px;cursor:pointer
  }
  #send{background:var(--vscode-button-background);color:var(--vscode-button-foreground);height:34px;padding:0 14px}
  .thinking{color:var(--vscode-descriptionForeground);font-style:italic;animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>
</head>
<body>
<div id="header">
  <span id="header-title">Cairn</span>
  <select id="project-select"><option value="">loading...</option></select>
  <span id="model-badge">connecting...</span>
</div>
<div id="messages"></div>
<div id="cost-row">$0.0000 | local: 0 | api: 0</div>
<div id="mentions-row"></div>
<div id="input-row">
  <textarea id="inp" placeholder="Ask Cairn... (@ to mention, Shift+Enter newline)" rows="1"></textarea>
  <select id="model-select">
    <option value="">Auto</option>
    <option value="local">Local</option>
    <option value="deepseek">DeepSeek</option>
    <option value="sonnet">Sonnet</option>
    <option value="opus">Opus</option>
  </select>
  <button id="send" class="btn-ok">Send</button>
</div>

<script>
const vscode = acquireVsCodeApi();
const msgs = document.getElementById('messages');
const inp  = document.getElementById('inp');
const send = document.getElementById('send');
const costRow  = document.getElementById('cost-row');
const modelBadge = document.getElementById('model-badge');
const projectSelect = document.getElementById('project-select');
const modelSelect = document.getElementById('model-select');
const mentionsRow = document.getElementById('mentions-row');

let sessionCost = 0, localCalls = 0, apiCalls = 0;
let pendingMentions = [];
let currentResponseDiv = null;
let currentActivityLog = null;
let activityItems = [];

// Project selector
projectSelect.addEventListener('change', () => {
  const val = projectSelect.value;
  if (val) {
    vscode.postMessage({ type: 'switchProject', projectId: val });
  }
});

function renderMentions(){
  mentionsRow.innerHTML = '';
  pendingMentions.forEach((m,i)=>{
    const icon = m.type==='file'?'\\u{1F4C4}':m.type==='folder'?'\\u{1F4C1}':m.type==='symbol'?'\\u2699':m.type==='session'?'\\u{1F4AC}':m.type==='core'?'\\u{1F4CC}':'\\u{1F310}';
    const pill = document.createElement('span');
    pill.className='mention-pill';
    pill.innerHTML=icon+' '+esc(m.display)+'<button onclick="removeMention('+i+')">\\u2715</button>';
    mentionsRow.appendChild(pill);
  });
}

function removeMention(i){ pendingMentions.splice(i,1); renderMentions(); }

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }

function md(text){
  if (!text) return '';
  return text
    .replace(/\`\`\`([\\s\\S]*?)\`\`\`/g, (_,c)=>'<pre>'+esc(c)+'</pre>')
    .replace(/\`([^\`]+)\`/g, (_,c)=>'<code>'+esc(c)+'</code>')
    .replace(/\\n/g,'<br>');
}

function toolIcon(tool) {
  if (tool === 'read_file') return '\\u{1F4D6}';
  if (tool === 'search_code') return '\\u{1F50D}';
  if (tool === 'edit_file' || tool === 'create_file') return '\\u270F\\uFE0F';
  if (tool === 'run_tests') return '\\u{1F9EA}';
  if (tool === 'run_command' || tool === 'run_migration') return '\\u26A1';
  if (tool.startsWith('git_')) return '\\u{1F4E6}';
  if (tool.startsWith('web_')) return '\\u{1F310}';
  return '\\u{1F527}';
}

function fmtDiff(diff){
  if(!diff || diff === 'No changes') return '<span class="git-status-clean">\\u2713 No changes</span>';
  return diff.split('\\n').map(l=>{
    if(l.startsWith('+++') || l.startsWith('---')) return '<span class="diff-file">'+esc(l)+'</span>';
    if(l.startsWith('@@')) return '<span class="diff-hunk">'+esc(l)+'</span>';
    if(l.startsWith('+')) return '<span class="add">'+esc(l)+'</span>';
    if(l.startsWith('-')) return '<span class="del">'+esc(l)+'</span>';
    return esc(l);
  }).join('\\n');
}

function addMsg(role, html, isHtml){
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  if(isHtml) d.innerHTML = html; else d.textContent = html;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
  return d;
}

function startResponse() {
  currentResponseDiv = document.createElement('div');
  currentResponseDiv.className = 'msg assistant';
  msgs.appendChild(currentResponseDiv);

  currentActivityLog = document.createElement('div');
  currentActivityLog.className = 'activity-log';
  currentActivityLog.style.display = 'none';
  currentResponseDiv.appendChild(currentActivityLog);
  activityItems = [];

  return currentResponseDiv;
}

function addActivityItem(text) {
  if (!currentActivityLog) return;
  currentActivityLog.style.display = 'block';
  const item = document.createElement('div');
  item.className = 'activity-item';
  item.textContent = text;
  currentActivityLog.appendChild(item);
  activityItems.push(item);
  msgs.scrollTop = msgs.scrollHeight;
}

function buildToolCard(tc){
  const risk = tc.risk_level || 'review';
  const icon = risk === 'destructive' ? '\\u26A0\\uFE0F' : '\\u{1F527}';
  const div = document.createElement('div');
  div.className = 'tool-card';

  let btnsHtml = '<div class="tool-btns">'
    + '<button class="btn-ok" onclick="approve(this)">Apply</button>'
    + '<button class="btn-cancel" onclick="reject(this)">Reject</button>';
  if (tc.tool_name === 'edit_file' && tc.diff_preview) {
    btnsHtml += '<button class="btn-cancel" onclick="showDiff(this)" style="margin-left:8px">View in VS Code</button>';
  }
  btnsHtml += '</div>';

  div.innerHTML =
    '<div class="tool-header">' + icon + ' ' + esc(tc.tool_name) + '</div>'
    + '<div class="tool-desc">' + esc(tc.description || '') + '</div>'
    + (tc.diff_preview ? '<div class="diff">' + fmtDiff(tc.diff_preview) + '</div>' : '')
    + btnsHtml;
  div._toolCall = tc;
  return div;
}

function approve(btn){
  const card = btn.closest('.tool-card');
  const tc = card._toolCall;
  card.querySelector('.tool-btns').innerHTML = '<em>Applying\\u2026</em>';
  vscode.postMessage({type:'approveTool', toolCallId:tc.tool_call_id, toolName:tc.tool_name, toolInput:tc.input});
}

function reject(btn){
  const card = btn.closest('.tool-card');
  const tc = card._toolCall;
  card.querySelector('.tool-btns').innerHTML = '<em>Rejected.</em>';
  vscode.postMessage({type:'rejectTool', toolCallId:tc.tool_call_id, toolName:tc.tool_name, toolInput:tc.input});
}

function showDiff(btn){
  const card = btn.closest('.tool-card');
  const tc = card._toolCall;
  vscode.postMessage({
    type:'showDiff',
    filePath: tc.input?.file_path || 'unknown',
    oldContent: tc.input?.old_str || '',
    newContent: tc.input?.new_str || '',
  });
}

function doSend(){
  const text = inp.value.trim();
  if(!text) return;
  addMsg('user', text, false);
  inp.value = ''; inp.style.height='auto';

  // Start streaming response
  startResponse();
  addActivityItem('Thinking...');

  const mentions = [...pendingMentions];
  pendingMentions = [];
  renderMentions();

  const override = modelSelect.value || undefined;
  vscode.postMessage({type:'sendMessage', content:text, mentions, modelOverride: override});
}

send.addEventListener('click', doSend);
inp.addEventListener('keydown', e=>{
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); doSend(); }
});
inp.addEventListener('input', ()=>{
  inp.style.height='auto';
  inp.style.height = Math.min(inp.scrollHeight,120)+'px';
});

window.addEventListener('message', e=>{
  const msg = e.data;

  switch(msg.type){
    case 'init': {
      // Populate project dropdown
      if (msg.projects && msg.projects.length) {
        projectSelect.innerHTML = '';
        msg.projects.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = p.id;
          if (p.id === msg.projectId) opt.selected = true;
          projectSelect.appendChild(opt);
        });
      } else {
        projectSelect.innerHTML = '<option>' + esc(msg.projectId) + '</option>';
      }
      sessionCost=0; localCalls=0; apiCalls=0;
      msgs.innerHTML='';
      costRow.textContent = '$0.0000 | local: 0 | api: 0';
      modelBadge.textContent = '\\u2014';
      addMsg('assistant','Ready. Project: ' + msg.projectId + ' | Session: ' + msg.sessionId.slice(0,8) + '\\u2026', false);
      break;
    }

    case 'newSession':
      msgs.innerHTML='';
      sessionCost=0; localCalls=0; apiCalls=0;
      addMsg('assistant','New session started.', false);
      break;

    case 'streamEvent': {
      const ev = msg.event;
      if (!ev) break;

      if (ev.type === 'routing') {
        if (currentActivityLog) {
          // Remove "Thinking..." placeholder
          const items = currentActivityLog.querySelectorAll('.activity-item');
          if (items.length === 1 && items[0].textContent === 'Thinking...') {
            items[0].remove();
          }
        }
        const tierLabel = ev.model || ('Tier ' + (ev.tier || '?'));
        addActivityItem('\\u{1F300} Routing: ' + tierLabel + (ev.manual ? ' [manual]' : ''));
        modelBadge.textContent = tierLabel;
      }

      if (ev.type === 'tool_start') {
        addActivityItem(toolIcon(ev.tool || '') + ' ' + (ev.tool || 'tool') + '...');
      }

      if (ev.type === 'tool_end') {
        const dur = ev.duration_ms ? ' (' + ev.duration_ms + 'ms)' : '';
        const chars = ev.result_chars ? ' ' + ev.result_chars + ' chars' : '';
        addActivityItem('\\u2705 ' + (ev.tool || 'tool') + dur + chars);
      }

      if (ev.type === 'tool_queued') {
        // REVIEW tool — show approval card
        if (currentResponseDiv && ev.pending_tool_call) {
          currentResponseDiv.appendChild(buildToolCard(ev.pending_tool_call));
        }
      }

      if (ev.type === 'complete') {
        // Final response
        if (currentResponseDiv) {
          // Add response text
          const textDiv = document.createElement('div');
          textDiv.innerHTML = md(ev.response || '');
          currentResponseDiv.appendChild(textDiv);

          // Pending tool call from complete event
          if (ev.pending_tool_call && !ev.pending_tool_call.auto_approve) {
            currentResponseDiv.appendChild(buildToolCard(ev.pending_tool_call));
          }

          // Footer: model + cost + chunks
          const footer = document.createElement('div');
          footer.className = 'msg-footer';
          const parts = [];
          if (ev.model_used) parts.push(ev.model_used);
          if (ev.cost_usd) parts.push('$' + ev.cost_usd.toFixed(4));
          if (ev.metadata?.memory?.chunks) parts.push(ev.metadata.memory.chunks + ' chunks');
          const toolCount = (ev.executed_tool_calls || []).length;
          if (toolCount) parts.push(toolCount + ' tool' + (toolCount > 1 ? 's' : ''));
          footer.textContent = parts.join(' \\u00B7 ');
          currentResponseDiv.appendChild(footer);
        }

        // Update cost tracking
        const mu = (ev.model_used || '').toLowerCase();
        const isLocal = mu.includes('qwen') || mu.includes('ollama') || mu.includes('local');
        if (isLocal) {
          localCalls++;
          modelBadge.textContent = '\\u26A1 local';
        } else if (mu.includes('opus')) {
          apiCalls++; sessionCost += ev.cost_usd || 0;
          modelBadge.textContent = '\\u{1F9E0} opus';
        } else if (mu.includes('deepseek')) {
          apiCalls++; sessionCost += ev.cost_usd || 0;
          modelBadge.textContent = '\\u2601 deepseek';
        } else {
          apiCalls++; sessionCost += ev.cost_usd || 0;
          modelBadge.textContent = '\\u2601 sonnet';
        }
        costRow.textContent = '$'+sessionCost.toFixed(4)+' | local: '+localCalls+' | api: '+apiCalls;
        msgs.scrollTop = msgs.scrollHeight;
      }

      if (ev.type === 'error') {
        addMsg('assistant', '\\u26A0 ' + (ev.message || ev.error || 'Unknown error'), false);
      }
      break;
    }

    case 'streamEnd':
      currentResponseDiv = null;
      currentActivityLog = null;
      break;

    case 'agentResponse':
      // Non-streaming fallback (e.g. tool approval responses)
      document.querySelectorAll('.thinking').forEach(el=>el.remove());
      {
        const d = document.createElement('div');
        d.className = 'msg assistant';
        d.innerHTML = md(msg.content);
        if(msg.pendingToolCall && !msg.pendingToolCall.auto_approve){
          d.appendChild(buildToolCard(msg.pendingToolCall));
        }
        msgs.appendChild(d);
        msgs.scrollTop = msgs.scrollHeight;
      }
      if(msg.modelUsed){
        const mu = msg.modelUsed.toLowerCase();
        const isLocal = mu.includes('qwen') || mu.includes('ollama') || mu.includes('local');
        if (isLocal) { localCalls++; }
        else { apiCalls++; sessionCost += msg.costUsd||0; }
        costRow.textContent = '$'+sessionCost.toFixed(4)+' | local: '+localCalls+' | api: '+apiCalls;
      }
      break;

    case 'error':
      document.querySelectorAll('.thinking').forEach(el=>el.remove());
      addMsg('assistant', '\\u26A0 ' + msg.message, false);
      break;

    case 'activeFileChanged':
      break;

    case 'addMention':
      if(msg.mention && !pendingMentions.find(m=>m.type===msg.mention.type&&m.value===msg.mention.value)){
        pendingMentions.push(msg.mention);
        renderMentions();
      }
      break;
  }
});

// Signal to extension that the webview is ready
vscode.postMessage({type:'ready'});
</script>
</body>
</html>`;
    }

    public dispose() {
        CairnPanel.currentPanel = undefined;
        this._panel.dispose();
        this._disposables.forEach(d => d.dispose());
        this._disposables = [];
    }
}
