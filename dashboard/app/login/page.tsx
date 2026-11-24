export default async function Login({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return (
    <main className="login">
      <form method="post" action="/api/login" className="card">
        <h1>FlyBot</h1>
        <label>Password <input type="password" name="password" autoFocus required autoComplete="current-password" /></label>
        {error && <p className="bad-text">Wrong password.</p>}
        <button type="submit">Sign in</button>
      </form>
    </main>
  );
}
