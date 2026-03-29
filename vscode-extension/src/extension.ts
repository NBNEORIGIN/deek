import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { CairnPanel } from './panel';

// ─── Helpers ────────────────────────────────────────────────────────────────

function getConfig() {
    const config = vscode.workspace.getConfiguration('cairn');
    const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
    let apiKey = config.get<string>('apiKey', '');

    // Fall back to reading D:\claw\.env if no key configured
    if (!apiKey) {
        try {
            const envPath = path.join('D:', 'claw', '.env');
            const envContent = fs.readFileSync(envPath, 'utf-8');
            const match = envContent.match(/^CLAW_API_KEY=(.+)$/m);
            if (match) { apiKey = match[1].trim(); }
        } catch { /* .env not found — continue without key */ }
    }

    const project = config.get<string>('defaultProject', '');
    return { apiUrl, apiKey, project };
}

// ─── Status bar ─────────────────────────────────────────────────────────────

let statusBarItem: vscode.StatusBarItem;
let statusBarInterval: ReturnType<typeof setInterval> | undefined;

async function updateStatusBar() {
    const { apiUrl, apiKey, project } = getConfig();
    try {
        const res = await fetch(`${apiUrl}/health`, {
            headers: apiKey ? { 'X-API-Key': apiKey } : {},
            signal: AbortSignal.timeout(5000),
        });
        if (!res.ok) {
            statusBarItem.text = '$(circle-slash) Cairn offline';
            statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
            return;
        }
        const data = await res.json() as {
            projects_loaded?: string[];
            total_chunks?: number;
        };
        const chunks = data.total_chunks ?? 0;
        const proj = project || (data.projects_loaded?.[0] ?? '');
        const cost = CairnPanel.currentPanel?.sessionCost ?? 0;
        statusBarItem.text = `$(circle-filled) Cairn  ${proj}  $${cost.toFixed(2)}  ${chunks.toLocaleString()} chunks`;
        statusBarItem.color = new vscode.ThemeColor('statusBarItem.foreground');
    } catch {
        statusBarItem.text = '$(circle-slash) Cairn offline';
        statusBarItem.color = new vscode.ThemeColor('statusBarItem.errorForeground');
    }
}

// ─── Inline completion provider ─────────────────────────────────────────────

let _completionDebounce: ReturnType<typeof setTimeout> | undefined;

class CairnInlineCompletionProvider implements vscode.InlineCompletionItemProvider {
    async provideInlineCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
        _context: vscode.InlineCompletionContext,
        token: vscode.CancellationToken,
    ): Promise<vscode.InlineCompletionList | undefined> {
        const enabled = vscode.workspace.getConfiguration('cairn')
            .get<boolean>('enableInlineCompletions', true);
        if (!enabled) { return undefined; }

        const debounceMs = vscode.workspace.getConfiguration('cairn')
            .get<number>('completionDebounceMs', 800);

        return new Promise((resolve) => {
            if (_completionDebounce) {
                clearTimeout(_completionDebounce);
            }
            _completionDebounce = setTimeout(async () => {
                if (token.isCancellationRequested) {
                    resolve(undefined);
                    return;
                }

                const { apiUrl, apiKey, project } = getConfig();

                // Limit prefix/suffix size
                const fullText = document.getText();
                const offset = document.offsetAt(position);
                const prefix = fullText.slice(Math.max(0, offset - 500), offset);
                const suffix = fullText.slice(offset, offset + 200);

                try {
                    const res = await fetch(`${apiUrl}/complete`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                        },
                        body: JSON.stringify({
                            file_path: document.fileName,
                            prefix,
                            suffix,
                            project: project || 'claw',
                            language: document.languageId,
                        }),
                        signal: AbortSignal.timeout(3000),
                    });

                    if (!res.ok) {
                        resolve(undefined);
                        return;
                    }

                    const data = await res.json() as { completion: string; tier: number };
                    if (!data.completion || data.tier === 0) {
                        resolve(undefined);
                        return;
                    }

                    resolve(new vscode.InlineCompletionList([
                        new vscode.InlineCompletionItem(
                            data.completion,
                            new vscode.Range(position, position),
                        ),
                    ]));
                } catch {
                    resolve(undefined);
                }
            }, debounceMs);
        });
    }
}

// ─── Extension activation ───────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left, 100
    );
    statusBarItem.command = 'cairn.openPanel';
    statusBarItem.text = '$(loading~spin) Cairn';
    statusBarItem.tooltip = 'Click to open Cairn panel';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    updateStatusBar();
    statusBarInterval = setInterval(updateStatusBar, 30_000);
    context.subscriptions.push({ dispose: () => { if (statusBarInterval) { clearInterval(statusBarInterval); } } });

    // Inline completions
    context.subscriptions.push(
        vscode.languages.registerInlineCompletionItemProvider(
            { pattern: '**' },
            new CairnInlineCompletionProvider(),
        )
    );

    // Open panel
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.openPanel', () => {
            CairnPanel.createOrShow(context.extensionUri);
        }),
    );

    // New session
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.newSession', () => {
            if (CairnPanel.currentPanel) {
                CairnPanel.currentPanel.newSession();
            } else {
                CairnPanel.createOrShow(context.extensionUri);
            }
        }),
    );

    // Index project
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.indexProject', async () => {
            const { apiUrl, apiKey, project } = getConfig();
            if (!project) {
                vscode.window.showErrorMessage(
                    'Set cairn.defaultProject in VS Code settings first'
                );
                return;
            }
            try {
                const res = await fetch(
                    `${apiUrl}/projects/${project}/index`,
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                        },
                        body: JSON.stringify({ force: false }),
                    }
                );
                if (res.ok) {
                    vscode.window.showInformationMessage(`Cairn: Indexing ${project} started`);
                } else {
                    vscode.window.showErrorMessage(`Cairn: Indexing failed — ${res.status}`);
                }
            } catch {
                vscode.window.showErrorMessage(`Cairn: Cannot reach API at ${apiUrl}`);
            }
        }),
    );

    // Add @ mention
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.addMention', async () => {
            const { apiUrl, apiKey, project } = getConfig();
            const projectId = project || 'claw';

            const typeChoice = await vscode.window.showQuickPick(
                [
                    { label: '📄 Current file', value: 'current_file', description: 'Pin the active editor file' },
                    { label: '📄 Pick a file...', value: 'file', description: 'Pin a specific file' },
                    { label: '📁 Pick a folder...', value: 'folder', description: 'Pin all files in a folder' },
                    { label: '🔍 Search symbols...', value: 'symbol', description: 'Pin a function or class by name' },
                    { label: '📋 Recent session', value: 'session', description: 'Attach a past session' },
                    { label: '📌 core.md', value: 'core', description: 'Attach the project core.md' },
                    { label: '🌐 Web search', value: 'web', description: 'Search the web and attach results' },
                ],
                { placeHolder: 'What context do you want to pin?' },
            );
            if (!typeChoice) { return; }

            let value = '';
            let display = '';

            if (typeChoice.value === 'current_file') {
                const editor = vscode.window.activeTextEditor;
                if (!editor) {
                    vscode.window.showWarningMessage('No active editor');
                    return;
                }
                value = editor.document.uri.fsPath;
                display = path.basename(value);
            } else if (typeChoice.value === 'core') {
                value = 'core.md';
                display = 'core.md';
            } else if (typeChoice.value === 'web') {
                const query = await vscode.window.showInputBox({ prompt: 'Web search query' });
                if (!query) { return; }
                value = query;
                display = query.slice(0, 30);
            } else if (typeChoice.value === 'file' || typeChoice.value === 'folder') {
                try {
                    const res = await fetch(`${apiUrl}/projects/${projectId}/files`, {
                        headers: apiKey ? { 'X-API-Key': apiKey } : {},
                    });
                    const data = await res.json() as { files: string[] };
                    let items = (data.files || []).map((f: string) => ({ label: f }));
                    if (typeChoice.value === 'folder') {
                        const dirs = new Set<string>();
                        data.files.forEach((f: string) => {
                            const parts = f.split('/');
                            if (parts.length > 1) { dirs.add(parts[0]); }
                        });
                        items = Array.from(dirs).map(d => ({ label: d + '/' }));
                    }
                    const picked = await vscode.window.showQuickPick(items, {
                        placeHolder: `Select a ${typeChoice.value}`,
                        matchOnDescription: true,
                    });
                    if (!picked) { return; }
                    value = picked.label.replace(/\/$/, '');
                    display = value.split('/').pop() || value;
                } catch {
                    vscode.window.showErrorMessage('Cairn: Could not fetch file list');
                    return;
                }
            } else if (typeChoice.value === 'symbol') {
                const query = await vscode.window.showInputBox({ prompt: 'Symbol name (function or class)' });
                if (!query) { return; }
                try {
                    const res = await fetch(
                        `${apiUrl}/projects/${projectId}/symbols?q=${encodeURIComponent(query)}`,
                        { headers: apiKey ? { 'X-API-Key': apiKey } : {} },
                    );
                    const data = await res.json() as { symbols: Array<{ name: string; file: string; type: string }> };
                    if (!data.symbols?.length) {
                        vscode.window.showWarningMessage(`No symbols found matching "${query}"`);
                        return;
                    }
                    const picked = await vscode.window.showQuickPick(
                        data.symbols.map(s => ({ label: s.name, description: `${s.type} in ${s.file}` })),
                        { placeHolder: 'Select symbol' },
                    );
                    if (!picked) { return; }
                    value = picked.label;
                    display = picked.label;
                } catch {
                    vscode.window.showErrorMessage('Cairn: Could not fetch symbols');
                    return;
                }
            } else if (typeChoice.value === 'session') {
                try {
                    const res = await fetch(
                        `${apiUrl}/projects/${projectId}/sessions`,
                        { headers: apiKey ? { 'X-API-Key': apiKey } : {} },
                    );
                    const data = await res.json() as { sessions: Array<{ session_id: string; first_message?: string }> };
                    const picked = await vscode.window.showQuickPick(
                        (data.sessions || []).map(s => ({
                            label: s.session_id.slice(0, 16),
                            description: s.first_message?.slice(0, 60) || '',
                            value: s.session_id,
                        })),
                        { placeHolder: 'Select session to attach' },
                    );
                    if (!picked) { return; }
                    value = (picked as any).value;
                    display = picked.label;
                } catch {
                    vscode.window.showErrorMessage('Cairn: Could not fetch sessions');
                    return;
                }
            }

            if (!value) { return; }

            const mentionType = typeChoice.value === 'current_file' ? 'file' : typeChoice.value;
            if (CairnPanel.currentPanel) {
                CairnPanel.currentPanel.addMention({ type: mentionType, value, display });
                vscode.window.showInformationMessage(`Cairn: Pinned ${mentionType} — ${display}`);
            } else {
                vscode.window.showWarningMessage('Cairn panel is not open');
            }
        }),
    );

    // Select / switch project
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.selectProject', async () => {
            const { apiUrl, apiKey } = getConfig();
            try {
                const res = await fetch(`${apiUrl}/projects`, {
                    headers: apiKey ? { 'X-API-Key': apiKey } : {},
                });
                const data = await res.json() as { projects: Array<{ id: string; name: string; ready: boolean }> };
                const items = data.projects
                    .filter(p => p.ready)
                    .map(p => ({ label: p.id, description: p.name }));

                const picked = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select Cairn project',
                });

                if (picked) {
                    const config = vscode.workspace.getConfiguration('cairn');
                    await config.update('defaultProject', picked.label, vscode.ConfigurationTarget.Global);
                    if (CairnPanel.currentPanel) {
                        CairnPanel.currentPanel.switchProject(picked.label);
                    }
                    vscode.window.showInformationMessage(`Cairn: Switched to project '${picked.label}'`);
                    updateStatusBar();
                }
            } catch {
                vscode.window.showErrorMessage(`Cairn: Cannot reach API at ${apiUrl}`);
            }
        }),
    );

    // WIGGUM autonomous run
    context.subscriptions.push(
        vscode.commands.registerCommand('cairn.runWiggum', async () => {
            const { apiUrl, apiKey, project } = getConfig();
            if (!project) {
                vscode.window.showErrorMessage('Set cairn.defaultProject in VS Code settings first');
                return;
            }

            const goal = await vscode.window.showInputBox({
                prompt: 'Enter goal for autonomous run',
                placeHolder: 'e.g. Fix the vision routing bug in core/models/router.py',
            });
            if (!goal) { return; }

            try {
                const res = await fetch(`${apiUrl}/wiggum`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
                    },
                    body: JSON.stringify({ project_id: project, goal }),
                });

                if (!res.ok) {
                    vscode.window.showErrorMessage(`WIGGUM: Failed to start — ${res.status}`);
                    return;
                }

                const data = await res.json() as { run_id: string };
                const runId = data.run_id;

                vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: `WIGGUM running: ${goal.slice(0, 50)}...`,
                        cancellable: false,
                    },
                    async () => {
                        // Poll for completion
                        for (let i = 0; i < 360; i++) {  // up to 30 minutes
                            await new Promise(r => setTimeout(r, 5000));
                            try {
                                const poll = await fetch(`${apiUrl}/wiggum/${runId}`, {
                                    headers: apiKey ? { 'X-API-Key': apiKey } : {},
                                    signal: AbortSignal.timeout(5000),
                                });
                                if (!poll.ok) { continue; }
                                const status = await poll.json() as {
                                    status: string;
                                    criteria_passed?: number;
                                    criteria_total?: number;
                                    error?: string;
                                };
                                if (status.status === 'complete') {
                                    vscode.window.showInformationMessage(
                                        `WIGGUM complete: ${status.criteria_passed ?? 0}/${status.criteria_total ?? 0} criteria passed`
                                    );
                                    return;
                                }
                                if (status.status === 'failed' || status.status === 'stuck') {
                                    vscode.window.showErrorMessage(
                                        `WIGGUM ${status.status}: ${status.error || 'inspect logs'}`
                                    );
                                    return;
                                }
                            } catch { /* retry */ }
                        }
                        vscode.window.showWarningMessage('WIGGUM: Timed out after 30 minutes');
                    },
                );
            } catch {
                vscode.window.showErrorMessage(`Cairn: Cannot reach API at ${apiUrl}`);
            }
        }),
    );
}

export function deactivate() {
    if (statusBarInterval) {
        clearInterval(statusBarInterval);
    }
}
