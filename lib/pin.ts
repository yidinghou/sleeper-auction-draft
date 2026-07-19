import bcrypt from "bcryptjs";
import crypto from "crypto";

export function generatePin(): string {
  return crypto.randomInt(0, 1_000_000).toString().padStart(6, "0");
}

export function hashPin(pin: string): Promise<string> {
  return bcrypt.hash(pin, 10);
}

export function verifyPin(pin: string, hash: string): Promise<boolean> {
  return bcrypt.compare(pin, hash);
}
