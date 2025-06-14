<script lang="ts">
	import { login } from '$lib/stores/auth';
	import { createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	export let visible = false;

	let password = '';
	let error = '';
	let isLoading = false;

	async function handleLogin() {
		if (!password.trim()) {
			error = 'Password is required';
			return;
		}

		isLoading = true;
		error = '';

		const result = await login(password);

		if (result.success) {
			password = '';
			dispatch('authenticated');
		} else {
			error = result.error || 'Login failed';
		}

		isLoading = false;
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			handleLogin();
		}
	}

	// Clear error when password changes
	$: if (password) {
		error = '';
	}
</script>

{#if visible}
	<div
		class="modal-overlay"
		role="button"
		tabindex="0"
		on:click|self={() => dispatch('close')}
		on:keydown={(e) => e.key === 'Escape' && dispatch('close')}
	>
		<div class="modal">
			<div class="modal-header">
				<h2>Authentication Required</h2>
				<p>Please enter the password to access the CSSFDLP interface</p>
			</div>

			<div class="modal-content">
				<div class="form-group">
					<label for="password">Password:</label>
					<input
						id="password"
						type="password"
						bind:value={password}
						on:keydown={handleKeydown}
						disabled={isLoading}
						placeholder="Enter password"
						autocomplete="current-password"
					/>
				</div>

				{#if error}
					<div class="error-message">{error}</div>
				{/if}
			</div>

			<div class="modal-actions">
				<button on:click={handleLogin} disabled={isLoading || !password.trim()} class="primary">
					{#if isLoading}
						Authenticating...
					{:else}
						Login
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-overlay {
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background: rgba(0, 0, 0, 0.7);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
	}

	.modal {
		background: white;
		border-radius: 12px;
		padding: 0;
		width: 90%;
		max-width: 450px;
		box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
		animation: modalSlideIn 0.3s ease-out;
	}

	@keyframes modalSlideIn {
		from {
			opacity: 0;
			transform: translateY(-20px) scale(0.95);
		}
		to {
			opacity: 1;
			transform: translateY(0) scale(1);
		}
	}

	.modal-header {
		padding: 24px 24px 16px;
		border-bottom: 1px solid #e5e7eb;
	}

	.modal-header h2 {
		margin: 0 0 8px 0;
		color: #1f2937;
		font-size: 1.5rem;
		font-weight: 600;
	}

	.modal-header p {
		margin: 0;
		color: #6b7280;
		font-size: 0.95rem;
	}

	.modal-content {
		padding: 24px;
	}

	.form-group {
		margin-bottom: 16px;
	}

	label {
		display: block;
		margin-bottom: 8px;
		font-weight: 500;
		color: #374151;
		font-size: 0.95rem;
	}

	input {
		width: 100%;
		padding: 12px 16px;
		border: 2px solid #e5e7eb;
		border-radius: 8px;
		font-size: 1rem;
		transition: border-color 0.2s ease;
		box-sizing: border-box;
	}

	input:focus {
		outline: none;
		border-color: #3b82f6;
		box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
	}

	input:disabled {
		background-color: #f9fafb;
		cursor: not-allowed;
	}

	.error-message {
		color: #dc2626;
		font-size: 0.9rem;
		margin-top: 8px;
		padding: 8px 12px;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 6px;
	}

	.modal-actions {
		padding: 16px 24px 24px;
		display: flex;
		justify-content: flex-end;
	}

	button {
		padding: 12px 24px;
		border: none;
		border-radius: 8px;
		font-size: 1rem;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s ease;
		min-width: 120px;
	}

	button.primary {
		background: #3b82f6;
		color: white;
	}

	button.primary:hover:not(:disabled) {
		background: #2563eb;
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
	}

	button:disabled {
		background: #9ca3af;
		cursor: not-allowed;
		transform: none;
		box-shadow: none;
	}
</style>
