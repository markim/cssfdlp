// site/src/routes/api/stream/reset/+server.ts
import { scriptStore } from '../store';
import { verifyToken, extractToken } from '$lib/auth';

export async function POST({ request }) {
	// Check authentication
	const authHeader = request.headers.get('Authorization');
	const token = extractToken(authHeader);

	if (!token || !verifyToken(token)) {
		return new Response(JSON.stringify({ error: 'Unauthorized' }), {
			status: 401,
			headers: { 'Content-Type': 'application/json' }
		});
	}

	// Only allow reset if script is not currently running
	if (scriptStore.isRunning) {
		return new Response(
			JSON.stringify({
				success: false,
				message: 'Cannot reset while script is running'
			}),
			{
				status: 400,
				headers: {
					'Content-Type': 'application/json'
				}
			}
		);
	}

	// Reset the script state and log
	scriptStore.reset();

	return new Response(
		JSON.stringify({
			success: true,
			message: 'Script state has been reset'
		}),
		{
			headers: {
				'Content-Type': 'application/json'
			}
		}
	);
}
