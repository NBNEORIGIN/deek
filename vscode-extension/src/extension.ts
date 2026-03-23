import * as vscode from 'vscode';
import { ClawPanel } from './panel';

export function activate(context: vscode.ExtensionContext) {

    context.subscriptions.push(
        vscode.commands.registerCommand('claw.openPanel', () => {
            ClawPanel.createOrShow(context.extensionUri);
        }),

        vscode.commands.registerCommand('claw.newSession', () => {
            if (ClawPanel.currentPanel) {
                ClawPanel.currentPanel.newSession();
            } else {
                ClawPanel.createOrShow(context.extensionUri);
            }
        }),

        vscode.commands.registerCommand('claw.indexProject', async () => {
            const config = vscode.workspace.getConfiguration('claw');
            const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
            const apiKey = config.get<string>('apiKey', '');
            const projectId = config.get<string>('defaultProject', '');

            if (!projectId) {
                vscode.window.showErrorMessage(
                    'Set claw.defaultProject in VS Code settings first'
                );
                return;
            }

            try {
                const res = await fetch(
                    `${apiUrl}/projects/${projectId}/index`,
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-API-Key': apiKey,
                        },
                        body: JSON.stringify({ force: false }),
                    }
                );
                if (res.ok) {
                    vscode.window.showInformationMessage(
                        `CLAW: Indexing ${projectId} started`
                    );
                } else {
                    vscode.window.showErrorMessage(
                        `CLAW: Indexing failed — ${res.status}`
                    );
                }
            } catch (err) {
                vscode.window.showErrorMessage(
                    `CLAW: Cannot reach API at ${apiUrl}`
                );
            }
        }),

        vscode.commands.registerCommand('claw.switchProject', async () => {
            const config = vscode.workspace.getConfiguration('claw');
            const apiUrl = config.get<string>('apiUrl', 'http://localhost:8765');
            const apiKey = config.get<string>('apiKey', '');

            try {
                const res = await fetch(`${apiUrl}/projects`, {
                    headers: { 'X-API-Key': apiKey },
                });
                const data = await res.json() as { projects: Array<{ id: string; name: string; ready: boolean }> };
                const items = data.projects
                    .filter(p => p.ready)
                    .map(p => ({ label: p.id, description: p.name }));

                const picked = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select CLAW project',
                });

                if (picked) {
                    await config.update(
                        'defaultProject', picked.label,
                        vscode.ConfigurationTarget.Global
                    );
                    if (ClawPanel.currentPanel) {
                        ClawPanel.currentPanel.switchProject(picked.label);
                    }
                    vscode.window.showInformationMessage(
                        `CLAW: Switched to project '${picked.label}'`
                    );
                }
            } catch {
                vscode.window.showErrorMessage(
                    `CLAW: Cannot reach API at ${apiUrl}`
                );
            }
        }),
    );
}

export function deactivate() {}
