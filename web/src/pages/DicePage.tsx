// M10 R3: the campaign's game log with a free roller.
import { GameLog } from "../components/GameLog";
import { useCampaign } from "../hooks";

export function DicePage() {
  const { campaign } = useCampaign();
  return (
    <section className="board dice-page">
      <header className="board-head">
        <div>
          <h1>Dice</h1>
          <p className="muted">
            Every roll is made on the server and shown here for the whole table. Rolls marked
            "to DM only" are seen by the roller and the DM. Roll from a character sheet to use
            its bonuses.
          </p>
        </div>
      </header>
      <GameLog cid={campaign.id} roller />
    </section>
  );
}
