import jwt from 'jsonwebtoken';

const JWT_SECRET = process.env.JWT_SECRET || 'your-secret-key-change-this-in-production';

export function verifyToken(token: string): boolean {
	try {
		jwt.verify(token, JWT_SECRET);
		return true;
	} catch (error) {
		return false;
	}
}

export function extractToken(authHeader: string | null): string | null {
	if (!authHeader || !authHeader.startsWith('Bearer ')) {
		return null;
	}
	return authHeader.substring(7);
}
