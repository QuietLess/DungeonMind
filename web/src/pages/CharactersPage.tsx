// Characters and their storyboards: anyone sees the character list; each storyboard shows the
// entries the viewer may read; only the character's player adds to it.
import { type FormEvent, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Drawer } from "../components/Drawer";
import { EntryCard } from "../components/EntryCard";
import { EntryEditor } from "../components/EntryEditor";
import { EntryPanel } from "../components/EntryPanel";
import { message, useCampaign, useLoad } from "../hooks";
import { PlusIcon } from "../icons";

export function CharactersPage() {
  const { campaign, user } = useCampaign();
  const { chid } = useParams();
  const navigate = useNavigate();
  const selectedId = chid ? Number(chid) : null;
  const { data: characters, reload: reloadCharacters } = useLoad(
    () => api.characters(campaign.id),
    [campaign.id],
  );
  const { data: entries, reload } = useLoad(
    () =>
      selectedId === null
        ? Promise.resolve([])
        : api.entries(campaign.id, { board: "character", character_id: selectedId }),
    [campaign.id, selectedId],
  );
  const [creatingCharacter, setCreatingCharacter] = useState(false);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [asNpc, setAsNpc] = useState(false);
  const [monster, setMonster] = useState("");
  const [monsterHits, setMonsterHits] = useState<string[]>([]);
  const [npcError, setNpcError] = useState<string | null>(null);
  const [addingNpc, setAddingNpc] = useState(false);
  const isDm = campaign.role === "dm";
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const sending = useRef(false);
  const [dirty, setDirty] = useState(false);

  const selected = characters?.find((c) => c.id === selectedId);
  const mine = selected?.owner_id === user.id;

  const createCharacter = async (e: FormEvent) => {
    e.preventDefault();
    if (sending.current) return; // F2
    sending.current = true;
    setSaving(true);
    setFormError(null);
    try {
      const made = await api.createCharacter(campaign.id, name, summary, isDm && asNpc);
      setCreatingCharacter(false);
      setName("");
      setSummary("");
      setAsNpc(false);
      reloadCharacters();
      navigate(`/c/${campaign.id}/characters/${made.id}`);
    } catch (err) {
      setFormError(message(err)); // F1: shown inside the open form
    } finally {
      sending.current = false;
      setSaving(false);
    }
  };

  return (
    <section className="board">
      <header className="board-head">
        <div>
          <h1>Characters</h1>
          <p className="muted">
            Each character has a storyboard: backstory, goals, bonds and secrets. Entries are
            shared with the DM by default.
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setCreatingCharacter(true)}>
          <PlusIcon /> New character
        </button>
      </header>
      <div className="character-layout">
        <div className="character-col">
        <ul className="character-list" aria-label="Characters">
          {characters?.filter((c) => !c.is_npc).length === 0 && <li className="muted">No characters yet.</li>}
          {characters?.filter((c) => !c.is_npc).map((c) => (
            <li key={c.id}>
              <Link
                to={`/c/${campaign.id}/characters/${c.id}`}
                className={c.id === selectedId ? "character-tile active" : "character-tile"}
                aria-current={c.id === selectedId ? "page" : undefined}
              >
                <span className="character-name">{c.name}</span>
                {c.summary && <span className="muted">{c.summary}</span>}
                {c.owner_id === user.id && <span className="role-tag">Yours</span>}
              </Link>
            </li>
          ))}
        </ul>
        {isDm && (
          <section className="npc-section" aria-labelledby="npc-h">
            <h2 id="npc-h">NPCs <small className="muted">only you see these</small></h2>
            <ul className="character-list" aria-label="NPCs">
              {characters?.filter((c) => c.is_npc).map((c) => (
                <li key={c.id}>
                  <Link
                    to={`/c/${campaign.id}/characters/${c.id}`}
                    className={c.id === selectedId ? "character-tile active" : "character-tile"}
                    aria-current={c.id === selectedId ? "page" : undefined}
                  >
                    <span className="character-name">{c.name}</span>
                    <span className="role-tag">NPC</span>
                  </Link>
                </li>
              ))}
            </ul>
            <form
              className="monster-add"
              onSubmit={async (e) => {
                e.preventDefault();
                if (addingNpc) return;
                setAddingNpc(true);
                setNpcError(null);
                try {
                  // Exactly what was typed or picked; the server says if it isn't an SRD monster.
                  const made = await api.npcsFromMonster(campaign.id, monster.trim(), 1);
                  setMonster("");
                  setMonsterHits([]);
                  reloadCharacters();
                  if (made[0]) navigate(`/c/${campaign.id}/characters/${made[0].id}/sheet`);
                } catch (err) {
                  setNpcError(message(err));
                } finally {
                  setAddingNpc(false);
                }
              }}
            >
              <label>
                NPC from an SRD monster
                <input
                  value={monster}
                  list="srd-monsters"
                  placeholder="e.g. Bandit Captain"
                  onChange={(e) => {
                    setMonster(e.target.value);
                    const q = e.target.value.trim();
                    if (q.length >= 2) api.monsters(campaign.id, q).then(setMonsterHits).catch(() => setMonsterHits([]));
                  }}
                />
                <datalist id="srd-monsters">
                  {monsterHits.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </label>
              {npcError && <p className="error" role="alert">{npcError}</p>}
              <button className="btn btn-small" disabled={!monster.trim() || addingNpc}>
                Add NPC
              </button>
            </form>
          </section>
        )}
        </div>
        <div className="storyboard">
          {!selected && <p className="empty">Pick a character to see their storyboard.</p>}
          {selected && (
            <>
              <header className="board-head">
                <div>
                  <h2>{selected.name}'s storyboard</h2>
                  {selected.summary && <p className="muted">{selected.summary}</p>}
                </div>
                <div className="actions">
                  <Link to={`/c/${campaign.id}/characters/${selected.id}/sheet`} className="btn btn-primary">
                    Character sheet
                  </Link>
                  {mine && (
                    <button type="button" className="btn" onClick={() => setAdding(true)}>
                      <PlusIcon /> Add to storyboard
                    </button>
                  )}
                </div>
              </header>
              {entries?.length === 0 && (
                <p className="empty">
                  {mine ? "Nothing here yet. Start with a backstory." : "Nothing you can see yet."}
                </p>
              )}
              <div className="entry-grid">
                {entries?.map((entry) => (
                  <EntryCard
                    key={entry.id}
                    entry={entry}
                    role={campaign.role}
                    userId={user.id}
                    onOpen={(e) => setOpen(e.id)}
                    onReveal={() => undefined}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {open !== null && <EntryPanel entryId={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {adding && selected && (
        <Drawer
          title={`Add to ${selected.name}'s storyboard`}
          onClose={() => setAdding(false)}
          dirty={dirty}
        >
          <EntryEditor
            board="character"
            characterId={selected.id}
            onSaved={() => {
              setAdding(false);
              reload();
            }}
            onCancel={() => setAdding(false)}
            onDirtyChange={setDirty}
          />
        </Drawer>
      )}
      {creatingCharacter && (
        <Drawer
          title="New character"
          onClose={() => setCreatingCharacter(false)}
          dirty={name !== "" || summary !== ""}
        >
          <form className="editor" onSubmit={createCharacter}>
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} />
            </label>
            <label>
              Summary {asNpc ? "(only you see NPCs)" : "(visible to everyone)"}
              <input value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={500} />
            </label>
            {isDm && (
              <label className="check">
                <input type="checkbox" checked={asNpc} onChange={(e) => setAsNpc(e.target.checked)} /> NPC (only you
                can see it; roll for it from the dice tray or combat)
              </label>
            )}
            {formError && (
              <p className="error" role="alert">
                {formError}
              </p>
            )}
            <div className="actions">
              <button type="button" className="btn btn-ghost" onClick={() => setCreatingCharacter(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={saving}>
                {saving ? "Creating…" : "Create character"}
              </button>
            </div>
          </form>
        </Drawer>
      )}
    </section>
  );
}
