import { useEffect, useState } from "react";

export default function App() {
  const [notes, setNotes] = useState([]);
  const [text, setText] = useState("");
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const res = await fetch("/api/notes");
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      setNotes(body);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    setText("");
    load();
  };

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 480, margin: "3rem auto", padding: "0 1rem" }}>
      <h1>Demo Notes</h1>
      <form onSubmit={add} style={{ display: "flex", gap: 8 }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a note" style={{ flex: 1 }} />
        <button>Add</button>
      </form>
      {error && <p style={{ color: "crimson" }}>Could not load notes: {error}</p>}
      <ul>
        {notes.map((n) => (
          <li key={n.id}>{n.text}</li>
        ))}
      </ul>
    </main>
  );
}
