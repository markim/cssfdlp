// site/src/routes/api/stream/+server.ts
import path from 'path';
import fs from 'fs/promises';
import { spawn } from 'child_process';
import type { RequestEvent } from '@sveltejs/kit';
import type { ReadableStreamDefaultController } from 'stream/web';
import { scriptStore } from './store';
import { verifyToken, extractToken } from '$lib/auth';

export const DELETE = async ({ request }: RequestEvent) => {
	// Check authentication
	const authHeader = request.headers.get('Authorization');
	const token = extractToken(authHeader);

	if (!token || !verifyToken(token)) {
		return new Response(JSON.stringify({ error: 'Unauthorized' }), {
			status: 401,
			headers: { 'Content-Type': 'application/json' }
		});
	}

	if (scriptStore.scriptProcess) {
		scriptStore.killProcess();
		return new Response(JSON.stringify({ message: 'Script execution cancelled' }), {
			headers: { 'Content-Type': 'application/json' }
		});
	}

	return new Response(JSON.stringify({ message: 'No script running' }), {
		headers: { 'Content-Type': 'application/json' }
	});
};

// Function to start the script execution (separate from streaming)
async function startScriptExecution(options: {
	useRemoteZip: boolean;
	remoteHost?: string;
	remoteUser?: string;
	remotePassword?: string;
	remotePath?: string;
	remoteZipUrl?: string;
}): Promise<void> {
	if (scriptStore.isRunning) {
		throw new Error('Script is already running');
	}

	try {
		scriptStore.clearLog();
		scriptStore.setRunning(true);

		// Determine the root directory of the project (one level up from 'site')
		const projectRoot = path.resolve(process.cwd(), '..');
		const venvPath = path.join(projectRoot, '.venv');
		const requirementsPath = path.join(projectRoot, 'requirements.txt');
		const scriptPath = path.join(projectRoot, 'cssfdlp.py');

		// Platform-specific executable paths
		const pythonVenvExecutableName = process.platform === 'win32' ? 'python.exe' : 'python3';
		const pipVenvExecutableName = process.platform === 'win32' ? 'pip.exe' : 'pip';

		const pythonVenvPath = path.join(
			venvPath,
			process.platform === 'win32' ? 'Scripts' : 'bin',
			pythonVenvExecutableName
		);
		const pipVenvPath = path.join(
			venvPath,
			process.platform === 'win32' ? 'Scripts' : 'bin',
			pipVenvExecutableName
		);

		// Step 1: Check/Create virtual environment
		try {
			await fs.stat(venvPath);
			scriptStore.addToLog('Virtual environment found', 'setup');
		} catch (error: any) {
			if (error.code === 'ENOENT') {
				scriptStore.addToLog('Creating virtual environment...', 'setup');

				const systemPythonExecutable = process.platform === 'win32' ? 'python' : 'python3';

				const venvProcess = spawn(systemPythonExecutable, ['-m', 'venv', path.basename(venvPath)], {
					cwd: projectRoot
				});

				venvProcess.stdout?.on('data', (data) => {
					scriptStore.addToLog(data.toString(), 'setup');
				});

				venvProcess.stderr?.on('data', (data) => {
					scriptStore.addToLog(`ERROR: ${data.toString()}`, 'setup');
				});

				await new Promise<void>((resolve, reject) => {
					venvProcess.on('close', (code) => {
						if (code === 0) {
							scriptStore.addToLog('Virtual environment created successfully', 'setup');
							resolve();
						} else {
							scriptStore.addToLog(
								`Failed to create virtual environment (exit code: ${code})`,
								'error'
							);
							reject(new Error(`Failed to create virtual environment (exit code: ${code})`));
						}
					});
				});
			} else {
				throw new Error(`Failed to check virtual environment: ${error.message}`);
			}
		}

		// Step 2: Install dependencies
		scriptStore.addToLog('Installing dependencies...', 'setup');

		const pipProcess = spawn(pipVenvPath, ['install', '-r', requirementsPath], {
			cwd: projectRoot
		});
		pipProcess.stdout?.on('data', (data) => {
			const output = data.toString();
			// Filter out verbose pip output to reduce noise
			const lines = output.split('\n');
			for (const line of lines) {
				const trimmedLine = line.trim();
				if (
					trimmedLine &&
					!trimmedLine.includes('Requirement already satisfied') &&
					!trimmedLine.includes('pip install --upgrade pip') &&
					!trimmedLine.includes('notice]') &&
					!trimmedLine.startsWith('Using cached') &&
					!trimmedLine.includes('Installing collected packages')
				) {
					scriptStore.addToLog(trimmedLine, 'setup');
				}
			}
		});
		pipProcess.stderr?.on('data', (data) => {
			const output = data.toString();
			// Filter out pip upgrade notices and other noise
			const lines = output.split('\n');
			for (const line of lines) {
				const trimmedLine = line.trim();
				if (
					trimmedLine &&
					!trimmedLine.includes('A new release of pip is available') &&
					!trimmedLine.includes('To update, run:') &&
					!trimmedLine.includes('notice]')
				) {
					scriptStore.addToLog(`ERROR: ${trimmedLine}`, 'setup');
				}
			}
		});

		await new Promise<void>((resolve, reject) => {
			pipProcess.on('close', (code) => {
				if (code === 0) {
					scriptStore.addToLog('Dependencies installed successfully', 'setup');
					resolve();
				} else {
					scriptStore.addToLog(`Failed to install dependencies (exit code: ${code})`, 'error');
					reject(new Error(`Failed to install dependencies (exit code: ${code})`));
				}
			});
		});

		// Step 3: Build script arguments and execute
		scriptStore.addToLog('Starting script execution...', 'execute');

		// Base arguments starting with -u for unbuffered output
		const args = ['-u', scriptPath];

		// Add arguments based on the provided parameters
		if (options.useRemoteZip) {
			if (options.remoteZipUrl) {
				args.push('--remote-zip-url', options.remoteZipUrl);
				scriptStore.addToLog(`Using remote zip URL: ${options.remoteZipUrl}`, 'setup');
			} else {
				args.push('--create-remote-zip');
				if (options.remoteHost) args.push('--remote-host', options.remoteHost);
				if (options.remoteUser) args.push('--remote-user', options.remoteUser);
				if (options.remotePassword) args.push('--remote-password', options.remotePassword);
				if (options.remotePath) args.push('--remote-path', options.remotePath);

				scriptStore.addToLog(`Creating zip on remote server: ${options.remoteHost}`, 'setup');
			}
		} else {
			// Default mode - script will expect a local zip file
			args.push('./cstrike.zip'); // Default path, script will handle missing file
			scriptStore.addToLog('Using local zip file (if available)', 'setup');
		}

		// Log the command being executed (excluding password)
		const logArgs = [...args];
		const pwIndex = logArgs.indexOf('--remote-password');
		if (pwIndex !== -1 && pwIndex + 1 < logArgs.length) {
			logArgs[pwIndex + 1] = '****';
		}
		scriptStore.addToLog(`Executing: ${pythonVenvPath} ${logArgs.join(' ')}`, 'setup');

		const scriptProcess = spawn(pythonVenvPath, args, {
			cwd: projectRoot,
			stdio: ['ignore', 'pipe', 'pipe'],
			env: { ...process.env, PYTHONUNBUFFERED: '1' }
		});

		// Store the process in the global store
		scriptStore.setScriptProcess(scriptProcess);

		if (!scriptProcess.stdout || !scriptProcess.stderr) {
			throw new Error('Failed to create script process streams');
		}

		scriptProcess.stdout.on('data', (data) => {
			scriptStore.addToLog(data.toString(), 'output');
		});

		scriptProcess.stderr.on('data', (data) => {
			scriptStore.addToLog(`ERROR: ${data.toString()}`, 'error');
		});

		scriptProcess.on('close', (code) => {
			if (code === 0) {
				scriptStore.addToLog('Script execution completed successfully', 'complete');
			} else {
				scriptStore.addToLog(`Script execution failed with code ${code}`, 'error');
			}
			scriptStore.setRunning(false);
		});
	} catch (error: any) {
		scriptStore.addToLog(`Error: ${error.message}`, 'error');
		scriptStore.setRunning(false);
		throw error;
	}
}

export const GET = async ({ url, request }: RequestEvent) => {
	// Check authentication - try header first, then query param for SSE
	const authHeader = request.headers.get('Authorization');
	let token = extractToken(authHeader);

	// If no token in header, check query params (for EventSource)
	if (!token) {
		token = url.searchParams.get('token');
	}

	if (!token || !verifyToken(token)) {
		return new Response(JSON.stringify({ error: 'Unauthorized' }), {
			status: 401,
			headers: { 'Content-Type': 'application/json' }
		});
	}

	// Extract query parameters for script configuration
	const useRemoteZip = url.searchParams.get('useRemoteZip') === 'true';
	const useDefaultServer = url.searchParams.get('useDefaultServer') === 'true';
	const clearCache = url.searchParams.get('clearCache') === 'true';

	let remoteHost = url.searchParams.get('remoteHost') || '';
	let remoteUser = url.searchParams.get('remoteUser') || '';
	let remotePassword = url.searchParams.get('remotePassword') || '';
	let remotePath = url.searchParams.get('remotePath') || '';
	const remoteZipUrl = url.searchParams.get('remoteZipUrl') || '';

	// If using default server, override with environment variables
	if (useDefaultServer && useRemoteZip && !remoteZipUrl) {
		remoteHost = process.env.REMOTE_HOST || '192.168.1.100';
		remoteUser = process.env.REMOTE_USER || 'root';
		remotePath = process.env.REMOTE_PATH || '/home/steam/css/cstrike';

		// Use environment password if no password provided or if using default server
		if (!remotePassword || useDefaultServer) {
			remotePassword = process.env.REMOTE_PASSWORD || '';
		}

		// Log that we're using default server configuration (without password)
		scriptStore.addToLog(`Using default server configuration: ${remoteHost}`, 'setup');
	}

	// Set up SSE response headers
	const headers = new Headers({
		'Content-Type': 'text/event-stream',
		'Cache-Control': 'no-cache',
		Connection: 'keep-alive'
	});
	let currentController: ReadableStreamDefaultController | null = null;

	const stream = new ReadableStream({
		start: async (controller: ReadableStreamDefaultController) => {
			currentController = controller;
			try {
				// Add this controller to our store
				scriptStore.addController(controller);

				// Start script execution only if not already running
				if (!scriptStore.isRunning) {
					await startScriptExecution({
						useRemoteZip,
						remoteHost,
						remoteUser,
						remotePassword,
						remotePath,
						remoteZipUrl
					});
				}
			} catch (error: any) {
				scriptStore.addToLog(`Error: ${error.message}`, 'error');
			}
		},
		cancel() {
			// Remove this specific controller from the store
			if (currentController) {
				scriptStore.removeController(currentController);
				currentController = null;
			}

			// The process should continue running for other clients
			// Only manual cancellation via DELETE should stop the process
		}
	});

	return new Response(stream, { headers });
};
