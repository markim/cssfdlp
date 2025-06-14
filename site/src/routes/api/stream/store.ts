// site/src/routes/api/stream/store.ts
import type { ReadableStreamDefaultController } from 'stream/web';
import type { ChildProcess } from 'child_process';

interface StreamController {
	controller: ReadableStreamDefaultController;
	active: boolean;
}

class ScriptStore {
	private static instance: ScriptStore;
	private _isRunning: boolean = false;
	private _log: { message: string; type: string }[] = [];
	private controllers: StreamController[] = [];
	private _scriptProcess: ChildProcess | null = null;

	private constructor() {}

	static getInstance(): ScriptStore {
		if (!ScriptStore.instance) {
			ScriptStore.instance = new ScriptStore();
		}
		return ScriptStore.instance;
	}

	get isRunning(): boolean {
		return this._isRunning;
	}

	get log(): { message: string; type: string }[] {
		return this._log;
	}

	get scriptProcess(): ChildProcess | null {
		return this._scriptProcess;
	}

	setScriptProcess(process: ChildProcess | null): void {
		this._scriptProcess = process;
	}

	killProcess(): void {
		if (this._scriptProcess) {
			this._scriptProcess.kill();
			this._scriptProcess = null;
			this.setRunning(false);
		}
	}
	addController(controller: ReadableStreamDefaultController): void {
		this.controllers.push({ controller, active: true });

		// Send the current log to the new controller
		this._log.forEach((entry) => {
			try {
				const event = {
					type: entry.type,
					message: entry.message
				};
				controller.enqueue(`event: message\ndata: ${JSON.stringify(event)}\n\n`);
			} catch (error) {
				console.log('Error sending existing log to new controller - controller may be closed');
				// Don't log the full error to avoid spam, just mark controller as inactive
			}
		});
	}

	removeController(controller: ReadableStreamDefaultController): void {
		const index = this.controllers.findIndex((c) => c.controller === controller);
		if (index !== -1) {
			this.controllers[index].active = false;
			this.controllers.splice(index, 1);
		}
	}

	getActiveControllerCount(): number {
		return this.controllers.filter((c) => c.active).length;
	}
	addToLog(message: string, type: string): void {
		const entry = { message: message.trim(), type };
		this._log.push(entry);

		// Send to all active controllers
		const event = {
			type: entry.type,
			message: entry.message
		}; // Filter out inactive controllers while sending messages
		this.controllers = this.controllers.filter(({ controller, active }) => {
			if (!active) return false;
			try {
				controller.enqueue(`event: message\ndata: ${JSON.stringify(event)}\n\n`);
				return true;
			} catch (error) {
				console.log('Controller is closed, removing it');
				// Controller is likely closed/disconnected, remove it
				return false;
			}
		});
	}

	clearLog(): void {
		this._log = [];
	}

	reset(): void {
		this._log = [];
		this._isRunning = false;
		this._scriptProcess = null;
		// Don't close controllers here - they might be waiting for new execution
	}
	setRunning(running: boolean): void {
		this._isRunning = running;
		if (!running) {
			this.addToLog('Script execution completed', 'complete');
			this._scriptProcess = null;
			// Close all controllers when the script completes
			this.controllers.forEach(({ controller }) => {
				try {
					controller.close();
				} catch (error) {
					console.error('Error closing controller:', error);
				}
			});
			this.controllers = [];
		}
	}

	getLog(): { message: string; type: string }[] {
		return this._log;
	}
}

// Export a singleton instance
export const scriptStore = ScriptStore.getInstance();

// Export convenience methods
export const isScriptRunning = () => scriptStore.isRunning;
export const addToLog = (message: string, type: string) => scriptStore.addToLog(message, type);
export const clearLog = () => scriptStore.clearLog();
export const resetScript = () => scriptStore.reset();
export const setScriptRunning = (running: boolean) => scriptStore.setRunning(running);
export const getLog = () => scriptStore.log;
