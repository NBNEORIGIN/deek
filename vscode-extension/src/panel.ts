import * as vscode from 'vscode';

export class ClawPanel {
    public static currentPanel: ClawPanel | undefined;

    private readonly _panel: vscode.WebviewPanel;
    private readonly _extensionUri: vscode.Uri;
    private _sessionId: string;
    private _projectId: string;
    private _disposables: vscode.Disposable[] = [];

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor
            ? vscode.ViewColumn.Beside
            : vscode.ViewColumn.One;

        if (ClawPanel.currentPanel) {
            ClawPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'clawAgent',
            'CLAW',
            column,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            }
        );

        ClawPanel.currentPanel = new ClawPanel(panel, extensionUri);
    }

    private constructor(
        panel: vscode.WebviewPanel,
        extensionUri: vscode.Uri,
    ) {
        this._panel = panel;
        this._extensionUri = extensionUri;
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        const config = vscode.workspace.getConfiguration('claw');
        this._projectId = config.get<string>('defaultProject', 'phloe');

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
                        await this._handleSend(message.content);
                        break;
                    case 'approveTool':
                        await this._handleApproval(message, true);
                        break;
                    case 'rejectTool':
                        await this._handleApproval(message, false);
                        break;
                    case 'ready':
                        // Panel loaded — send current project/file context
                        this._panel.webview.postMessage({
                            type: 'init',
                            projectId: this._projectId,
                            sessionId: this._sessionId,
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

    public newSession() {
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        this._panel.webview.postMessage({
            type: 'newSession',
            sessionId: this._sessionId,
        });
    }

    public switchProject(projectId: string) {
        this._projectId = projectId;
        this._sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        this._panel.webview.postMessage({
            type: 'init',
            projectId: this._projectId,
            sessionId: this._sessionId,
        });
    }

    public reveal() {
        this._panel.reveal();
    }

    private async _handleSend(content: string) {
        const config = vscode.workspace.getConfiguration('claw');
        const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
        const apiKey = config.get<string>('apiKey', '');

        const editor = vscode.window.activeTextEditor;
        const activeFile = editor?.document.uri.fsPath ?? null;
        const selection = editor?.selection;
        const selectedText = (selection && !selection.isEmpty)
            ? editor!.document.getText(selection)
            : null;

        try {
            const res = await fetch(`${apiUrl}/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': apiKey,
                },
                body: JSON.stringify({
                    content,
                    project_id: this._projectId,
                    session_id: this._sessionId,
                    channel: 'vscode',
                    active_file: activeFile,
                    selected_text: selectedText,
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

        } catch (err) {
            this._panel.webview.postMessage({
                type: 'error',
                message: `Cannot reach CLAW API at ${apiUrl}. Is it running?`,
            });
        }
    }

    private async _handleApproval(message: {
        toolCallId: string;
        toolName: string;
        toolInput: Record<string, unknown>;
    }, approved: boolean) {
        const config = vscode.workspace.getConfiguration('claw');
        const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
        const apiKey = config.get<string>('apiKey', '');

        try {
            const res = await fetch(`${apiUrl}/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': apiKey,
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
        } catch (err) {
            this._panel.webview.postMessage({
                type: 'error',
                message: `Approval failed: ${err}`,
            });
        }
    }

    private _getHtml(): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CLAW</title>
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
    display:flex;align-items:center;justify-content:space-between;
    font-size:11px;flex-shrink:0
  }
  #project-badge{
    font-weight:bold;color:var(--vscode-textLink-foreground);
    letter-spacing:.5px;text-transform:uppercase;font-size:10px
  }
  #model-badge{color:var(--vscode-descriptionForeground)}
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
  #cost-row{
    padding:3px 12px;font-size:10px;
    color:var(--vscode-descriptionForeground);
    background:var(--vscode-panel-background);
    border-top:1px solid var(--vscode-panel-border);
    flex-shrink:0
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
  #send{background:var(--vscode-button-background);color:var(--vscode-button-foreground);height:34px;padding:0 14px}
  .thinking{color:var(--vscode-descriptionForeground);font-style:italic;animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>
</head>
<body>
<div id="header">
  <span id="project-badge">CLAW</span>
  <span id="model-badge">connecting...</span>
</div>
<div id="messages"></div>
<div id="cost-row">$0.0000 | local: 0 | api: 0</div>
<div id="input-row">
  <textarea id="inp" placeholder="Ask CLAW..." rows="1"></textarea>
  <button id="send" class="btn-ok">Send</button>
</div>

<script>
const vscode = acquireVsCodeApi();
const msgs = document.getElementById('messages');
const inp  = document.getElementById('inp');
const send = document.getElementById('send');
const costRow  = document.getElementById('cost-row');
const modelBadge = document.getElementById('model-badge');
const projectBadge = document.getElementById('project-badge');

let sessionCost = 0, localCalls = 0, apiCalls = 0;

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }

function md(text){
  // minimal markdown: fenced code, inline code, newlines
  return text
    .replace(/\`\`\`([\\s\\S]*?)\`\`\`/g, (_,c)=>'<pre>'+esc(c)+'</pre>')
    .replace(/\`([^\`]+)\`/g, (_,c)=>'<code>'+esc(c)+'</code>')
    .replace(/\\n/g,'<br>');
}

function fmtDiff(diff){
  return diff.split('\\n').map(l=>{
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

function addResponse(content, toolCall){
  const d = document.createElement('div');
  d.className = 'msg assistant';
  d.innerHTML = md(content);
  if(toolCall && !toolCall.auto_approve){
    d.appendChild(buildToolCard(toolCall));
  }
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}

function buildToolCard(tc){
  const risk = tc.risk_level || 'review';
  const icon = risk === 'destructive' ? '⚠️' : '🔧';
  const div = document.createElement('div');
  div.className = 'tool-card';
  div.innerHTML = \`
    <div class="tool-header">\${icon} \${esc(tc.tool_name)}</div>
    <div class="tool-desc">\${esc(tc.description)}</div>
    \${tc.diff_preview ? '<div class="diff">'+fmtDiff(tc.diff_preview)+'</div>' : ''}
    <div class="tool-btns">
      <button class="btn-ok"  onclick="approve(this)">Apply</button>
      <button class="btn-cancel" onclick="reject(this)">Reject</button>
    </div>
  \`;
  div._toolCall = tc;
  return div;
}

function approve(btn){
  const card = btn.closest('.tool-card');
  const tc = card._toolCall;
  card.querySelector('.tool-btns').innerHTML = '<em>Applying…</em>';
  vscode.postMessage({type:'approveTool', toolCallId:tc.tool_call_id, toolName:tc.tool_name, toolInput:tc.input});
}

function reject(btn){
  const card = btn.closest('.tool-card');
  const tc = card._toolCall;
  card.querySelector('.tool-btns').innerHTML = '<em>Rejected.</em>';
  vscode.postMessage({type:'rejectTool', toolCallId:tc.tool_call_id, toolName:tc.tool_name, toolInput:tc.input});
}

function doSend(){
  const text = inp.value.trim();
  if(!text) return;
  addMsg('user', text, false);
  inp.value = ''; inp.style.height='auto';
  const thinking = addMsg('assistant','Thinking…',false);
  thinking.classList.add('thinking');
  vscode.postMessage({type:'sendMessage', content:text});
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
  document.querySelectorAll('.thinking').forEach(el=>el.remove());

  switch(msg.type){
    case 'init':
      projectBadge.textContent = 'CLAW / ' + msg.projectId;
      sessionCost=0; localCalls=0; apiCalls=0;
      msgs.innerHTML='';
      costRow.textContent = '$0.0000 | local: 0 | api: 0';
      modelBadge.textContent = '—';
      addMsg('assistant','Ready. Session: ' + msg.sessionId.slice(0,8) + '…', false);
      break;

    case 'newSession':
      msgs.innerHTML='';
      sessionCost=0; localCalls=0; apiCalls=0;
      addMsg('assistant','New session started.', false);
      break;

    case 'agentResponse':
      addResponse(msg.content, msg.pendingToolCall);
      if(msg.modelUsed){
        const isLocal = msg.modelUsed.toLowerCase().includes('qwen') ||
                        msg.modelUsed.toLowerCase().includes('local');
        modelBadge.textContent = isLocal ? '⚡ local' : '☁ claude';
        if(isLocal){ localCalls++; } else { apiCalls++; sessionCost += msg.costUsd||0; }
        costRow.textContent = '$'+sessionCost.toFixed(4)+' | local: '+localCalls+' | api: '+apiCalls;
      }
      break;

    case 'error':
      addMsg('assistant', '⚠ ' + msg.message, false);
      break;

    case 'activeFileChanged':
      // Could show in a status bar, for now no-op
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
        ClawPanel.currentPanel = undefined;
        this._panel.dispose();
        this._disposables.forEach(d => d.dispose());
        this._disposables = [];
    }
}
