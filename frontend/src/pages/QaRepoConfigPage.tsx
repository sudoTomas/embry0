import { Link, useParams } from "react-router";
import { QaConfigEditor } from "@/components/qa/QaConfigEditor";
import { PageError } from "@/components/PageError";

export function QaRepoConfigPage() {
  const { repo } = useParams<{ repo: string }>();
  const parts = (repo ?? "").split("/");
  const [owner, name] = parts;
  const validRepo = parts.length === 2 && !!owner && !!name;

  if (!validRepo) return <PageError message="Invalid repo — expected owner/name" />;

  return (
    <div className="space-y-4">
      <Link to={`/repos/${repo}`} className="text-sm text-white/50 hover:underline">
        ← Repository
      </Link>
      <h1 className="title-page text-xl font-bold">QA config</h1>
      <QaConfigEditor repo={`${owner}/${name}`} />
    </div>
  );
}
