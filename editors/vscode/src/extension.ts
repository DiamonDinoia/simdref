import * as fs from 'fs';
import * as path from 'path';
import * as child_process from 'child_process';
import * as os from 'os';
import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
    Trace,
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;
let output: vscode.OutputChannel | undefined;

const SERVER_REL_DIR = 'server-venv';
const BUNDLED_WHEEL_DIR = 'server-dist';

function getOutput(): vscode.OutputChannel {
    if (!output) {
        output = vscode.window.createOutputChannel('simdref');
    }
    return output;
}

function log(message: string): void {
    getOutput().appendLine(message);
}

function which(cmd: string): string | undefined {
    const pathEnv = process.env.PATH ?? '';
    const sep = process.platform === 'win32' ? ';' : ':';
    const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : [''];
    for (const dir of pathEnv.split(sep)) {
        if (!dir) continue;
        for (const ext of exts) {
            const candidate = path.join(dir, cmd + ext);
            try {
                fs.accessSync(candidate, fs.constants.X_OK);
                return candidate;
            } catch {
                // keep searching
            }
        }
    }
    return undefined;
}

function venvBinary(venvDir: string, name: string): string {
    if (process.platform === 'win32') {
        return path.join(venvDir, 'Scripts', `${name}.exe`);
    }
    return path.join(venvDir, 'bin', name);
}

function resolvePython(config: vscode.WorkspaceConfiguration): string | undefined {
    const configured = (config.get<string>('pythonPath') ?? '').trim();
    if (configured) {
        return configured;
    }
    return which('python3') ?? which('python');
}

function runCommand(
    command: string,
    args: string[],
    cwd?: string,
): Promise<void> {
    return new Promise((resolve, reject) => {
        log(`$ ${command} ${args.join(' ')}`);
        const proc = child_process.spawn(command, args, {
            cwd,
            env: process.env,
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        proc.stdout.on('data', (chunk) => log(chunk.toString().trimEnd()));
        proc.stderr.on('data', (chunk) => log(chunk.toString().trimEnd()));
        proc.on('error', (err) => reject(err));
        proc.on('exit', (code) => {
            if (code === 0) {
                resolve();
            } else {
                reject(new Error(`${command} exited with code ${code}`));
            }
        });
    });
}

function listBundledWheels(context: vscode.ExtensionContext): string[] {
    const dir = path.join(context.extensionPath, BUNDLED_WHEEL_DIR);
    if (!fs.existsSync(dir)) return [];
    return fs
        .readdirSync(dir)
        .filter((name) => name.endsWith('.whl'))
        .map((name) => path.join(dir, name));
}

async function ensureServer(
    context: vscode.ExtensionContext,
    config: vscode.WorkspaceConfiguration,
    forceReinstall = false,
): Promise<string> {
    const override = (config.get<string>('serverPath') ?? '').trim();
    if (override) {
        return override;
    }

    const storage = context.globalStorageUri.fsPath;
    fs.mkdirSync(storage, { recursive: true });
    const venvDir = path.join(storage, SERVER_REL_DIR);
    const serverBin = venvBinary(venvDir, 'simdref-lsp');
    const pythonBin = venvBinary(venvDir, process.platform === 'win32' ? 'python' : 'python3');

    if (!forceReinstall && fs.existsSync(serverBin)) {
        return serverBin;
    }

    if (forceReinstall && fs.existsSync(venvDir)) {
        fs.rmSync(venvDir, { recursive: true, force: true });
    }

    const bootstrapPython = resolvePython(config);
    if (!bootstrapPython) {
        throw new Error(
            'No Python interpreter found. Install Python 3.11+ (https://www.python.org/downloads/) ' +
                'or set simdref.pythonPath in settings.',
        );
    }

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'simdref: installing Python language server…',
            cancellable: false,
        },
        async (progress) => {
            progress.report({ message: 'creating virtualenv' });
            await runCommand(bootstrapPython, ['-m', 'venv', venvDir]);

            progress.report({ message: 'upgrading pip' });
            await runCommand(pythonBin, ['-m', 'pip', 'install', '--upgrade', 'pip', '--disable-pip-version-check']);

            const wheels = listBundledWheels(context);
            progress.report({ message: wheels.length ? 'installing bundled wheel' : 'installing simdref from PyPI' });
            const pipArgs = ['-m', 'pip', 'install', '--disable-pip-version-check'];
            if (wheels.length) {
                pipArgs.push(...wheels);
            } else {
                pipArgs.push('simdref');
            }
            await runCommand(pythonBin, pipArgs);
        },
    );

    if (!fs.existsSync(serverBin)) {
        throw new Error(
            `Server install completed but ${serverBin} was not produced. Run "simdref: Show Output" for details.`,
        );
    }
    return serverBin;
}

function buildInitializationOptions(config: vscode.WorkspaceConfiguration): Record<string, unknown> {
    return {
        showPerfMetrics: config.get<boolean>('showPerfMetrics', true),
        architectures: config.get<string[]>('architectures', []),
    };
}

async function startClient(context: vscode.ExtensionContext): Promise<void> {
    const config = vscode.workspace.getConfiguration('simdref');
    let serverPath: string;
    try {
        serverPath = await ensureServer(context, config);
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        log(`ERROR: ${message}`);
        await vscode.window.showErrorMessage(`simdref: ${message}`, 'Show Output').then((choice) => {
            if (choice === 'Show Output') {
                getOutput().show();
            }
        });
        return;
    }

    const serverOptions: ServerOptions = {
        run: { command: serverPath, args: [], transport: TransportKind.stdio },
        debug: { command: serverPath, args: [], transport: TransportKind.stdio },
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            { scheme: 'file', language: 'c' },
            { scheme: 'file', language: 'cpp' },
            { scheme: 'file', language: 'objective-c' },
            { scheme: 'file', language: 'objective-cpp' },
            { scheme: 'file', language: 'asm' },
        ],
        outputChannel: getOutput(),
        initializationOptions: buildInitializationOptions(config),
    };

    client = new LanguageClient('simdref', 'simdref', serverOptions, clientOptions);
    const trace = config.get<string>('trace.server', 'off');
    await client.setTrace(traceValue(trace));
    await client.start();
    log('simdref language server started');
}

function traceValue(setting: string): Trace {
    switch (setting) {
        case 'messages':
            return Trace.Messages;
        case 'verbose':
            return Trace.Verbose;
        default:
            return Trace.Off;
    }
}

async function stopClient(): Promise<void> {
    if (!client) return;
    await client.stop();
    client = undefined;
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    log(`simdref extension v${context.extension.packageJSON.version} activating on ${os.platform()}`);

    context.subscriptions.push(
        vscode.commands.registerCommand('simdref.restartServer', async () => {
            await stopClient();
            await startClient(context);
        }),
        vscode.commands.registerCommand('simdref.reinstallServer', async () => {
            await stopClient();
            const config = vscode.workspace.getConfiguration('simdref');
            try {
                await ensureServer(context, config, true);
                await startClient(context);
                vscode.window.showInformationMessage('simdref: server reinstalled.');
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                vscode.window.showErrorMessage(`simdref reinstall failed: ${message}`);
            }
        }),
        vscode.commands.registerCommand('simdref.showOutput', () => getOutput().show()),
        vscode.workspace.onDidChangeConfiguration((event) => {
            if (
                event.affectsConfiguration('simdref.serverPath') ||
                event.affectsConfiguration('simdref.pythonPath') ||
                event.affectsConfiguration('simdref.showPerfMetrics') ||
                event.affectsConfiguration('simdref.architectures') ||
                event.affectsConfiguration('simdref.trace.server')
            ) {
                vscode.commands.executeCommand('simdref.restartServer');
            }
        }),
    );

    await startClient(context);
}

export async function deactivate(): Promise<void> {
    await stopClient();
}
