<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { authToken, isAuthenticated, logout, getAuthHeaders } from '$lib/stores/auth';
	import AuthModal from '$lib/components/AuthModal.svelte';

	let message: string = '';
	let scriptOutput: string = '';
	let formattedLogLines: { text: string; type: string }[] = [];
	let isLoading: boolean = false;
	let outputElement: HTMLDivElement | null = null;
	let eventSource: EventSource | null = null;
	let isInitialized: boolean = false;
	let showStartModal: boolean = false;
	let showAuthModal: boolean = false;
	let currentToken: string | null = null;
	let clearCache: boolean = false;

	// Subscribe to auth token changes
	$: currentToken = $authToken;
	$: if ($isAuthenticated) {
		showAuthModal = false;
		initializeApp();
	}

	async function initializeApp() {
		if (!$isAuthenticated || !currentToken) {
			return;
		}

		try {
			const response = await fetch('/api/stream/status', {
				headers: getAuthHeaders(currentToken)
			});

			if (response.status === 401) {
				// Token is invalid, logout and show auth modal
				logout();
				showAuthModal = true;
				return;
			}

			const data = await response.json();

			if (data.isRunning) {
				isLoading = true;
				message = 'Process already running...';

				// Load existing log
				const logResponse = await fetch('/api/stream/log', {
					headers: getAuthHeaders(currentToken)
				});
				const logData = await logResponse.json();

				if (logData.log) {
					// Process existing log entries
					formattedLogLines = logData.log
						.map((entry: { message: string; type: string }) => ({
							text:
								entry.type && entry.type !== 'output'
									? `[${entry.type.toUpperCase()}] ${entry.message}`
									: entry.message,
							type: entry.type || 'output'
						}))
						.filter((line: { text: string }) => line.text.trim() !== '');

					// Connect to SSE stream to continue receiving updates
					connectToEventSource();
				}
			}
		} catch (error) {
			console.error('Error checking process status:', error);
		} finally {
			isInitialized = true;
		}
	}

	// Initialize on mount
	onMount(async () => {
		if ($isAuthenticated) {
			await initializeApp();
		} else {
			showAuthModal = true;
			isInitialized = true;
		}
	});

	// Helper to connect to the EventSource
	function connectToEventSource(params?: URLSearchParams) {
		if (!currentToken) {
			console.error('No auth token available for SSE connection');
			return;
		}

		// Close any existing connection
		if (eventSource) {
			eventSource.close();
		}

		// Add auth token to params
		if (!params) {
			params = new URLSearchParams();
		}
		params.append('token', currentToken);

		// Connect to the Server-Sent Events endpoint
		const sseUrl = `/api/stream?${params.toString()}`;
		eventSource = new EventSource(sseUrl);

		eventSource.onmessage = (event) => {
			try {
				const data = JSON.parse(event.data);
				let logType = 'info';

				// Update UI based on message type
				switch (data.type) {
					case 'setup':
						message = 'Setting up environment...';
						scriptOutput += `[SETUP] ${data.message}\n`;
						logType = 'setup';
						showStartModal = false;
						break;

					case 'execute':
						message = 'Executing script...';
						scriptOutput += `[EXECUTE] ${data.message}\n`;
						logType = 'execute';
						showStartModal = false;
						break;

					case 'output':
						message = 'Script running...';
						scriptOutput += `${data.message}\n`;
						logType = 'output';
						showStartModal = false;
						break;

					case 'error':
						message = 'Error occurred';
						scriptOutput += `[ERROR] ${data.message}\n`;
						logType = 'error';
						showStartModal = false;
						break;

					case 'complete':
						message = 'Script execution completed';
						scriptOutput += `[COMPLETE] ${data.message}\n`;
						logType = 'complete';
						isLoading = false;
						showStartModal = false;

						// Close the EventSource connection
						if (eventSource) {
							eventSource.close();
							eventSource = null;
						}
						break;

					default:
						scriptOutput += `${data.message}\n`;
						logType = 'default';
				}

				// Function to clean ANSI color codes and extract log type
				function cleanAnsiAndExtractType(rawMessage: string): {
					cleanMessage: string;
					extractedType: string | null;
				} {
					// Remove ANSI color codes (e.g., [35m, [0m, [32m, etc.)
					let cleanMessage = rawMessage.replace(/\x1b\[[0-9;]*m/g, '');

					// Extract log type from prefixes like [EXECUTE], [CONFIG], [INFO], etc.
					const typeMatch = cleanMessage.match(/^\[([A-Z]+)\]\s*/);
					let extractedType = null;

					if (typeMatch) {
						extractedType = typeMatch[1].toLowerCase();
						// Remove the prefix from the message
						cleanMessage = cleanMessage.replace(/^\[([A-Z]+)\]\s*/, '');
					}

					return { cleanMessage, extractedType };
				}

				// Function to add timestamp to messages
				function addTimestamp(message: string): string {
					const now = new Date();
					const timestamp = now.toLocaleTimeString('en-US', {
						hour12: false,
						hour: '2-digit',
						minute: '2-digit',
						second: '2-digit'
					});
					return `[${timestamp}] ${message}`;
				}

				// Process messages - split multiline messages into separate log lines
				const messages = data.message.split('\n');
				const newLines = messages
					.map((msg: string) => {
						if (msg.trim() === '') return null;

						const { cleanMessage, extractedType } = cleanAnsiAndExtractType(msg);

						// Determine the final log type - prefer extracted type over data.type
						let finalType = logType;
						if (extractedType) {
							// Map extracted types to our log types
							switch (extractedType) {
								case 'execute':
									finalType = 'execute';
									break;
								case 'setup':
									finalType = 'setup';
									break;
								case 'config':
									finalType = 'info';
									break;
								case 'progress':
									finalType = 'info';
									break;
								case 'success':
									finalType = 'setup';
									break;
								case 'error':
									finalType = 'error';
									break;
								case 'warning':
									finalType = 'error';
									break;
								case 'info':
								default:
									finalType = 'info';
									break;
							}
						}

						// Add prefix for non-output types and add timestamp
						let finalMessage;
						if (data.type && data.type !== 'output') {
							finalMessage = addTimestamp(`[${data.type.toUpperCase()}] ${cleanMessage}`);
						} else {
							// For output messages, check if we extracted a type
							if (extractedType) {
								finalMessage = addTimestamp(`[${extractedType.toUpperCase()}] ${cleanMessage}`);
							} else {
								finalMessage = addTimestamp(cleanMessage);
							}
						}

						return {
							text: finalMessage,
							type: finalType
						};
					})
					.filter((line: any) => line !== null);

				// Add the formatted lines to our array
				formattedLogLines = [...formattedLogLines, ...newLines];

				// Auto-scroll to the bottom of the output
				setTimeout(scrollToBottom, 10);
			} catch (error) {
				console.error('Error parsing SSE event:', error);
			}
		};

		eventSource.onerror = () => {
			message = 'Connection lost';
			isLoading = false;
			showStartModal = false;

			// Close the EventSource connection
			if (eventSource) {
				eventSource.close();
				eventSource = null;
			}
		};
	}

	// Helper to auto-scroll the output to the bottom when new content is added
	function scrollToBottom() {
		if (outputElement) {
			outputElement.scrollTop = outputElement.scrollHeight;
		}
	}

	// Clean up SSE connection when component unmounts
	onDestroy(() => {
		if (eventSource) {
			eventSource.close();
			eventSource = null;
		}
	});

	// Start the script execution and connect to the SSE stream
	async function handleUpdate() {
		if (!isInitialized || isLoading || !$isAuthenticated || !currentToken) {
			if (!$isAuthenticated) {
				showAuthModal = true;
			}
			return;
		}

		// Show modal with spinner
		showStartModal = true;

		// First clear any existing log state
		await clearLog();

		isLoading = true;
		message = 'Starting update process...';
		scriptOutput = '';
		formattedLogLines = [];

		// Build query parameters for the SSE endpoint
		// Always use remote zip creation with default server configuration
		const params = new URLSearchParams();
		params.append('useRemoteZip', 'true');
		params.append('useDefaultServer', 'true');
		params.append('clearCache', clearCache.toString());

		// Connect to the Server-Sent Events endpoint with parameters
		connectToEventSource(params);

		// Hide modal after a short delay or when first output is received
		setTimeout(() => {
			showStartModal = false;
		}, 3000);
	}

	// Function to cancel the running script
	async function cancelScript() {
		if (isLoading && currentToken) {
			try {
				const response = await fetch('/api/stream', {
					method: 'DELETE',
					headers: getAuthHeaders(currentToken)
				});

				const result = await response.json();
				message = result.message;
				scriptOutput += `\n[CANCELLED] Script execution cancelled by user\n`;

				// Add the cancellation message to the formatted log
				formattedLogLines = [
					...formattedLogLines,
					{
						text: `[CANCELLED] Script execution cancelled by user`,
						type: 'error'
					}
				];
			} catch (error) {
				console.error('Error cancelling script:', error);
			}

			isLoading = false;
			showStartModal = false;
			if (eventSource) {
				eventSource.close();
				eventSource = null;
			}
		}
	}

	// Function to retry/start new execution (replaces clear log functionality)
	async function retryExecution() {
		handleUpdate();
	}

	// Function to clear the log and reset for a new execution
	async function clearLog() {
		if (isLoading || !currentToken) {
			return; // Can't clear while script is running or without auth
		}

		try {
			const response = await fetch('/api/stream/reset', {
				method: 'POST',
				headers: getAuthHeaders(currentToken)
			});

			const result = await response.json();

			if (result.success) {
				// Reset the UI state
				message = '';
				scriptOutput = '';
				formattedLogLines = [];

				// Close any existing EventSource connection
				if (eventSource) {
					eventSource.close();
					eventSource = null;
				}
			} else {
				console.error('Failed to reset:', result.message);
			}
		} catch (error) {
			console.error('Error resetting log:', error);
		}
	}

	// Logout function
	function handleLogout() {
		// Close any existing connections
		if (eventSource) {
			eventSource.close();
			eventSource = null;
		}

		// Reset state
		isLoading = false;
		message = '';
		scriptOutput = '';
		formattedLogLines = [];
		showStartModal = false;

		// Logout
		logout();
		showAuthModal = true;
	}
</script>

<svelte:head>
	<title>Counter-Strike Source Server Updater</title>
	<meta name="description" content="Update your Counter-Strike Source server" />
</svelte:head>

<div class="containerstyle">
	<header>
		<div class="header-content">
			<div class="header-text">
				<h1>Counter-Strike Source Server Management</h1>
				<p class="header-subtitle">FastDL File Processor & S3 Uploader</p>
			</div>
			{#if $isAuthenticated}
				<button class="logout-button" on:click={handleLogout}> Logout </button>
			{/if}
		</div>
	</header>

	<main class="main-content">
		<div class="left-column">
			<section class="update-section">
				<h2>Server Update</h2>
				<p>
					This tool processes Counter-Strike Source server files for FastDL distribution. It
					compresses maps and audio files, generates MD5 checksums, and uploads everything to your
					S3-compatible storage.
				</p>

				<div class="workflow-info">
					<h4>Update Process:</h4>
					<ol>
						<li>Download/create zip from your CS:S server</li>
						<li>Extract and process game files (maps, sounds, etc.)</li>
						<li>Compress files with bzip2 for FastDL compatibility</li>
						<li>Generate MD5 checksums for file verification</li>
						<li>Upload to S3 bucket for client downloads</li>
					</ol>
				</div>

				<div class="config-section">
					<h3>Remote Server Processing</h3>
					<p>
						Authenticated sessions will automatically create and download a zip from the default
						server configuration, then process the files for FastDL distribution.
					</p>

					<div class="server-info">
						<p><strong>Processing Mode:</strong> Remote zip creation with default server</p>
						<p><strong>Authentication:</strong> Required (use login modal)</p>
					</div>

					<div class="cache-option">
						<label class="checkbox-label">
							<input type="checkbox" bind:checked={clearCache} />
							<span class="checkmark"></span>
							Clear cache before processing (force fresh start)
						</label>
						<p class="option-description">
							When enabled, will clear all cached files and start with a completely fresh processing run.
							Useful when you want to ensure all files are reprocessed from scratch.
						</p>
					</div>
				</div>

				<!-- Single action button that changes based on state -->
				{#if isLoading}
					<button class="cancel-button" on:click={cancelScript}> Cancel Execution </button>
				{:else if formattedLogLines.length > 0}
					<button class="retry-button" on:click={retryExecution} disabled={!isInitialized}>
						Retry Update
					</button>
				{:else}
					<button on:click={handleUpdate} disabled={!isInitialized || !$isAuthenticated}>
						{!isInitialized
							? 'Checking Status...'
							: !$isAuthenticated
								? 'Authentication Required'
								: 'Start Server Update'}
					</button>
				{/if}
			</section>
		</div>

		<div class="right-column">
			<section class="output-section">
				<h2>Script Output</h2>
				{#if message}
					<div
						class="status-message {message.startsWith('Error') || message.startsWith('Failed')
							? 'error'
							: ''}"
					>
						{message}
					</div>
				{/if}
				{#if formattedLogLines.length > 0}
					<div class="output-wrapper">
						<div class="output" bind:this={outputElement}>
							{#each formattedLogLines as { text, type }, index}
								<div class="log-line {type}" data-line-number={index + 1}>
									<span class="line-number">{index + 1}</span>
									<span class="log-content">{text}</span>
								</div>
							{/each}
						</div>
					</div>
				{:else if !isInitialized}
					<div class="no-output">Checking process status...</div>
				{:else}
					<div class="no-output">No output yet. Run the script to see results here.</div>
				{/if}
			</section>
		</div>
	</main>

	<footer>
		<p></p>
	</footer>
</div>

<!-- Authentication Modal -->
<AuthModal
	visible={showAuthModal}
	on:authenticated={() => {
		showAuthModal = false;
		initializeApp();
	}}
	on:close={() => (showAuthModal = false)}
/>

<!-- Loading Modal -->
{#if showStartModal}
	<div class="modal-overlay">
		<div class="modal-content">
			<div class="spinner"></div>
			<h3>Starting Update Process</h3>
			<p>Setting up environment and clearing previous logs...</p>
		</div>
	</div>
{/if}

<style>
	/* Aggressive reset to override any framework defaults */
	:global(*) {
		box-sizing: border-box;
	}

	:global(html, body) {
		margin: 0 !important;
		padding: 0 !important;
		width: 100% !important;
		height: 100% !important;
		overflow-x: hidden !important;
	}

	:global(#app, [data-sveltekit-preload-data], main) {
		width: 100% !important;
		height: 100% !important;
		margin: 0 !important;
		padding: 0 !important;
	}

	.containerstyle {
		font-family: 'Arial', sans-serif;
		width: 100vw;
		height: 100vh;
		margin: 0;
		padding: 15px;
		background-color: #333;
		color: #f0f0f0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		box-sizing: border-box;
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
	}

	header {
		flex-shrink: 0;
		margin-bottom: 20px;
		border-bottom: 2px solid #555;
		padding-bottom: 10px;
		text-align: center;
	}

	.header-content {
		display: flex;
		justify-content: space-between;
		align-items: center;
		max-width: 1200px;
		margin: 0 auto;
	}

	.header-text {
		text-align: left;
	}

	header h1 {
		color: #ff9900;
		font-size: 2.2em;
		text-shadow: 2px 2px 4px #000;
		margin: 0 0 5px 0;
	}

	.header-subtitle {
		color: #cccccc;
		font-size: 1em;
		margin: 0;
		font-weight: normal;
		text-shadow: 1px 1px 2px #000;
	}

	.logout-button {
		background: linear-gradient(135deg, #ff6b6b 0%, #ee5a5a 100%);
		color: white;
		border: none;
		padding: 10px 20px;
		border-radius: 6px;
		cursor: pointer;
		font-weight: 500;
		font-size: 0.9rem;
		transition: all 0.3s ease;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
	}

	.logout-button:hover {
		background: linear-gradient(135deg, #ff5252 0%, #e53e3e 100%);
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
	}

	.main-content {
		display: grid;
		grid-template-columns: 25% 75%;
		gap: 20px;
		flex: 1;
		min-height: 0; /* Important for grid items to shrink */
		overflow: hidden;
	}

	.left-column,
	.right-column {
		display: flex;
		flex-direction: column;
		min-height: 0; /* Important for flexbox items to shrink */
	}

	.update-section,
	.output-section {
		background-color: #444;
		padding: 20px;
		border-radius: 6px;
		box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.4);
		flex: 1;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		min-height: 0; /* Important for nested flex containers */
	}

	.update-section h2,
	.output-section h2 {
		color: #ffcc66;
		margin: 0 0 15px 0;
		font-size: 1.5em;
	}

	.update-section p {
		margin: 0 0 15px 0;
		font-size: 0.9em;
		line-height: 1.4;
	}

	.workflow-info {
		background-color: #3a3a3a;
		padding: 12px;
		border-radius: 5px;
		margin-bottom: 20px;
		border-left: 3px solid #2196f3;
	}

	.workflow-info h4 {
		color: #64b5f6;
		margin: 0 0 8px 0;
		font-size: 1em;
	}

	.workflow-info ol {
		margin: 0;
		padding-left: 20px;
		color: #e0e0e0;
	}

	.workflow-info li {
		font-size: 0.85em;
		line-height: 1.3;
		margin-bottom: 3px;
	}

	.config-section {
		margin-bottom: 20px;
		text-align: left;
		flex: 1;
		overflow-y: auto;
		min-height: 0; /* Important for scroll to work */
	}

	.server-info {
		background-color: #555;
		padding: 15px;
		border-radius: 5px;
		margin-top: 15px;
		border-left: 4px solid #ff9900;
	}

	.server-info p {
		margin: 8px 0;
		color: #e0e0e0;
	}

	.server-info strong {
		color: #ffcc66;
	}

	.checkbox-label {
		display: flex;
		align-items: center;
		font-size: 1em;
		margin-bottom: 12px;
		cursor: pointer;
	}

	.checkbox-label input[type='checkbox'] {
		margin-right: 8px;
		transform: scale(1.1);
	}

	.remote-config {
		background-color: #555;
		padding: 15px;
		border-radius: 5px;
		margin-top: 12px;
		border-left: 4px solid #ff9900;
	}

	.remote-config h3,
	.remote-config h4 {
		color: #ffcc66;
		margin: 0 0 12px 0;
		font-size: 1.1em;
	}

	.server-config {
		margin-top: 15px;
		padding-top: 12px;
		border-top: 1px solid #666;
	}

	.server-preset {
		background-color: #666;
		padding: 10px;
		border-radius: 4px;
		margin-bottom: 15px;
		border-left: 3px solid #28a745;
	}

	.default-server-info {
		background-color: #4a5d3a;
		padding: 12px;
		border-radius: 4px;
		margin-bottom: 15px;
		border-left: 3px solid #28a745;
	}

	.default-server-info p {
		margin: 4px 0;
		font-size: 0.9em;
		color: #e8f5e8;
	}

	.default-server-info small {
		color: #c3d9c3;
		font-style: italic;
		margin-top: 8px;
		display: block;
	}

	.custom-server-config {
		background-color: #4a4a4a;
		padding: 12px;
		border-radius: 4px;
		margin-bottom: 15px;
		border-left: 3px solid #ff9900;
	}

	.local-mode-info {
		background-color: #3a4a5a;
		padding: 12px;
		border-radius: 4px;
		margin-top: 15px;
		border-left: 3px solid #6c757d;
	}

	.local-mode-info h4 {
		color: #adb5bd;
		margin: 0 0 8px 0;
		font-size: 1em;
	}

	.local-mode-info p {
		margin: 0;
		font-size: 0.85em;
		line-height: 1.3;
		color: #d0d7de;
	}

	.local-mode-info code {
		background-color: #495057;
		padding: 2px 4px;
		border-radius: 2px;
		font-family: 'Courier New', monospace;
		font-size: 0.9em;
		color: #f8f9fa;
	}

	.input-group {
		margin-bottom: 12px;
	}

	.input-group label {
		display: block;
		margin-bottom: 4px;
		color: #f0f0f0;
		font-weight: bold;
		font-size: 0.9em;
	}

	.input-group input {
		width: 100%;
		padding: 6px 10px;
		border: 1px solid #666;
		border-radius: 4px;
		background-color: #333;
		color: #f0f0f0;
		font-size: 0.85em;
	}

	.input-group input:focus {
		outline: none;
		border-color: #ff9900;
		box-shadow: 0 0 5px rgba(255, 153, 0, 0.3);
	}

	.input-group input:disabled {
		background-color: #222;
		color: #888;
		cursor: not-allowed;
	}

	.input-group small {
		display: block;
		margin-top: 4px;
		color: #aaa;
		font-size: 0.75em;
		font-style: italic;
	}

	button {
		background-color: #ff9900;
		color: #222;
		border: none;
		padding: 10px 20px;
		font-size: 1em;
		font-weight: bold;
		border-radius: 5px;
		cursor: pointer;
		transition:
			background-color 0.3s ease,
			transform 0.2s ease;
		box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
		margin-top: 15px;
	}

	button:hover:not(:disabled) {
		background-color: #e68a00;
		transform: translateY(-2px);
	}

	button:disabled {
		background-color: #777;
		cursor: not-allowed;
	}

	.cancel-button {
		background-color: #cc3300;
		color: #fff;
		margin-top: 10px;
		font-size: 0.9em;
		padding: 8px 16px;
	}

	.cancel-button:hover {
		background-color: #ff4000;
	}

	.retry-button {
		background-color: #28a745;
		color: #fff;
		margin-top: 10px;
		font-size: 1em;
		padding: 10px 20px;
		font-weight: bold;
	}

	.retry-button:hover:not(:disabled) {
		background-color: #218838;
		transform: translateY(-2px);
	}

	.status-message {
		margin-top: 15px;
		padding: 10px;
		background-color: #555;
		border-left: 5px solid #ff9900;
		border-radius: 4px;
		text-align: left;
		font-size: 0.9em;
	}

	.status-message.error {
		border-left-color: #cc3300;
		color: #ffdddd;
		background-color: #604040;
	}

	.output-section {
		text-align: left;
		position: relative; /* For positioning the scroll indicator */
	}

	.output-wrapper {
		position: relative;
		flex: 1;
		display: flex;
		flex-direction: column;
		min-height: 0;
		border: 1px solid #555;
		border-radius: 4px;
		background-color: #222;
		overflow: hidden; /* Hide overflow except for the .output element */
	}

	.output {
		background-color: #222;
		padding: 0;
		font-family: 'Courier New', Courier, monospace;
		font-size: 0.85em;
		line-height: 1.4;
		overflow-y: auto;
		overflow-x: auto; /* Enable horizontal scrolling */
		flex: 1;
		color: #f0f0f0;
		min-height: 0; /* Important for scroll to work */
		max-height: none; /* Remove any height restrictions */
		width: 100%; /* Ensure it takes full width */
	}

	/* Horizontal scroll indicator */
	.output::-webkit-scrollbar {
		height: 8px; /* Height of the horizontal scrollbar */
	}

	.output::-webkit-scrollbar-track {
		background: #333;
		border-radius: 0 0 4px 4px;
	}

	.output::-webkit-scrollbar-thumb {
		background: #555;
		border-radius: 4px;
	}

	.output::-webkit-scrollbar-thumb:hover {
		background: #777;
	}

	.output {
		background-color: #222;
		border: 1px solid #555;
		border-radius: 4px;
		padding: 0;
		font-family: 'Courier New', Courier, monospace;
		font-size: 0.85em;
		line-height: 1.4;
		overflow-y: auto;
		overflow-x: auto; /* Enable horizontal scrolling */
		flex: 1;
		color: #f0f0f0;
		min-height: 0; /* Important for scroll to work */
		max-height: none; /* Remove any height restrictions */
		width: 100%; /* Ensure it takes full width */
	}

	.log-line {
		display: flex;
		align-items: flex-start;
		padding: 4px 5px;
		border-bottom: 1px solid #333;
		transition: background-color 0.2s ease;
		font-family: 'Courier New', Courier, monospace;
		white-space: nowrap; /* Prevent line breaks */
		min-width: max-content; /* Allow content to extend beyond viewport */
	}

	.log-line:hover {
		background-color: #333;
	}

	.log-line:nth-child(even) {
		background-color: #282828;
	}

	.line-number {
		min-width: 40px;
		color: #888;
		font-size: 0.9em;
		margin-right: 10px;
		text-align: right;
		user-select: none;
		padding-right: 8px;
		border-right: 1px solid #444;
	}

	.log-content {
		flex: 0 0 auto; /* Don't allow shrinking */
		white-space: nowrap; /* Prevent text wrapping */
		overflow: visible; /* Allow text to extend beyond container */
		padding-left: 8px;
	}

	/* Color coding for different log types */
	.log-line.info {
		color: #e1f5fe;
		border-left: 3px solid #2196f3;
	}

	.log-line.setup {
		color: #d1e7dd;
		border-left: 3px solid #198754;
	}

	.log-line.execute {
		color: #fff3cd;
		border-left: 3px solid #ffc107;
	}

	.log-line.output {
		color: #f8f9fa;
		border-left: 3px solid #6c757d;
	}

	.log-line.error {
		color: #f8d7da;
		border-left: 3px solid #dc3545;
	}

	.log-line.complete {
		color: #d1e7dd;
		border-left: 3px solid #198754;
		font-weight: bold;
	}

	footer {
		flex-shrink: 0;
		margin-top: 20px;
		padding-top: 10px;
		border-top: 1px solid #555;
		font-size: 0.8em;
		color: #aaa;
		text-align: center;
	}

	/* Modal styles */
	.modal-overlay {
		position: fixed;
		top: 0;
		left: 0;
		width: 100%;
		height: 100%;
		background-color: rgba(0, 0, 0, 0.7);
		display: flex;
		justify-content: center;
		align-items: center;
		z-index: 1000;
	}

	.modal-content {
		background-color: #444;
		padding: 30px;
		border-radius: 8px;
		text-align: center;
		box-shadow: 0 8px 32px rgba(0, 0, 0, 0.8);
		max-width: 400px;
		width: 90%;
	}

	.modal-content h3 {
		color: #ff9900;
		margin: 15px 0 10px 0;
		font-size: 1.4em;
	}

	.modal-content p {
		color: #f0f0f0;
		margin: 0;
		font-size: 0.9em;
	}

	/* Spinner animation */
	.spinner {
		width: 40px;
		height: 40px;
		border: 4px solid #666;
		border-top: 4px solid #ff9900;
		border-radius: 50%;
		animation: spin 1s linear infinite;
		margin: 0 auto 15px auto;
	}

	@keyframes spin {
		0% {
			transform: rotate(0deg);
		}
		100% {
			transform: rotate(360deg);
		}
	}

	/* Responsive design for smaller screens */
	@media (max-width: 1200px) {
		.main-content {
			grid-template-columns: 30% 70%;
		}
	}

	@media (max-width: 768px) {
		.main-content {
			grid-template-columns: 1fr;
			gap: 15px;
		}

		.header-content {
			flex-direction: column;
			gap: 15px;
		}

		.header-text {
			text-align: center;
		}

		header h1 {
			font-size: 1.8em;
		}
	}
</style>
