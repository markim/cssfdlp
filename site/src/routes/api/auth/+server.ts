import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import jwt from 'jsonwebtoken';

// In production, this should be an environment variable
const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key-change-this-in-production';
const VALID_PASSWORD = process.env.REMOTE_PASSWORD || 'admin'; // Same as your main app

export const POST: RequestHandler = async ({ request }) => {
	try {
		const { password } = await request.json();

		if (!password) {
			return json({ error: 'Password is required' }, { status: 400 });
		}

		// Validate password against the same password used in your main app
		if (password !== VALID_PASSWORD) {
			return json({ error: 'Invalid password' }, { status: 401 });
		}

		// Generate JWT token
		const token = jwt.sign(
			{
				authenticated: true,
				timestamp: Date.now()
			},
			JWT_SECRET,
			{ expiresIn: '24h' }
		);

		return json({ token });
	} catch (error) {
		console.error('Auth error:', error);
		return json({ error: 'Internal server error' }, { status: 500 });
	}
};
