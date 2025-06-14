// site/src/routes/api/stream/log/+server.ts
import { scriptStore } from '../store';
import { verifyToken, extractToken } from '$lib/auth';

export async function GET({ request }) {
	// Check authentication
	const authHeader = request.headers.get('Authorization');
	const token = extractToken(authHeader);

	if (!token || !verifyToken(token)) {
		return new Response(JSON.stringify({ error: 'Unauthorized' }), {
			status: 401,
			headers: { 'Content-Type': 'application/json' }
		});
	}

	return new Response(JSON.stringify({ log: scriptStore.log }), {
		headers: {
			'Content-Type': 'application/json'
		}
	});
}
