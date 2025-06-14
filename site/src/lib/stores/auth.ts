import { writable } from 'svelte/store';
import { browser } from '$app/environment';

// Store for authentication token
export const authToken = writable<string | null>(null);

// Store for authentication status
export const isAuthenticated = writable(false);

// Initialize from sessionStorage if in browser
if (browser) {
	const stored = sessionStorage.getItem('authToken');
	if (stored) {
		authToken.set(stored);
		isAuthenticated.set(true);
	}
}

// Subscribe to token changes and update sessionStorage
authToken.subscribe((token) => {
	if (browser) {
		if (token) {
			sessionStorage.setItem('authToken', token);
			isAuthenticated.set(true);
		} else {
			sessionStorage.removeItem('authToken');
			isAuthenticated.set(false);
		}
	}
});

// Function to login
export async function login(password: string): Promise<{ success: boolean; error?: string }> {
	try {
		const response = await fetch('/api/auth', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({ password })
		});

		const data = await response.json();

		if (response.ok && data.token) {
			authToken.set(data.token);
			return { success: true };
		} else {
			return { success: false, error: data.error || 'Login failed' };
		}
	} catch (error) {
		return { success: false, error: 'Network error' };
	}
}

// Function to logout
export function logout() {
	authToken.set(null);
}

// Function to get auth headers for fetch requests
export function getAuthHeaders(token: string | null): Record<string, string> {
	const headers: Record<string, string> = {
		'Content-Type': 'application/json'
	};

	if (token) {
		headers['Authorization'] = `Bearer ${token}`;
	}

	return headers;
}
