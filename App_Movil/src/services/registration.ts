// After Auth succeeds, profile failures must not trigger another account creation.
export async function completeRegistration<User>(operations: {
  create: () => Promise<User>; saveName: (user: User) => Promise<void>; ensureProfile: (user: User) => Promise<void>;
}) {
  const user = await operations.create(); const pending: ('name' | 'profile')[] = [];
  try { await operations.saveName(user); } catch { pending.push('name'); }
  try { await operations.ensureProfile(user); } catch { pending.push('profile'); }
  return { user, pending };
}
