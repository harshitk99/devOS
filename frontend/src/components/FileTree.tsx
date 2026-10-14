import { useMemo, useState } from "react";
import type { FileEntry } from "../api/types";

export type Flag = "finding" | "change";

interface Node {
  name: string;
  path: string;
  isFile: boolean;
  children: Map<string, Node>;
}

function buildTree(files: FileEntry[]): Node {
  const root: Node = { name: "", path: "", isFile: false, children: new Map() };
  for (const { path } of files) {
    let node = root;
    const parts = path.split("/");
    parts.forEach((part, i) => {
      const childPath = parts.slice(0, i + 1).join("/");
      if (!node.children.has(part)) {
        node.children.set(part, { name: part, path: childPath, isFile: i === parts.length - 1, children: new Map() });
      }
      node = node.children.get(part)!;
    });
  }
  return root;
}

/** Folders first, then files, each alphabetical. */
function sorted(node: Node): Node[] {
  return [...node.children.values()].sort(
    (a, b) => Number(a.isFile) - Number(b.isFile) || a.name.localeCompare(b.name),
  );
}

interface Props {
  files: FileEntry[];
  selected: string | null;
  flags: Record<string, Flag>;
  onSelect: (path: string) => void;
}

export function FileTree({ files, selected, flags, onSelect }: Props) {
  const tree = useMemo(() => buildTree(files), [files]);
  // Small projects open fully; big ones open only the folders that hold something flagged.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const collapseByDefault = files.length > 80;

  const isOpen = (node: Node) => {
    if (collapsed.has(node.path)) return false;
    if (!collapseByDefault) return true;
    return Object.keys(flags).some((p) => p.startsWith(node.path + "/")) || (selected ?? "").startsWith(node.path + "/");
  };
  const toggle = (node: Node) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (isOpen(node)) next.add(node.path);
      else next.delete(node.path);
      return next;
    });

  const render = (node: Node, depth: number): React.ReactNode =>
    sorted(node).map((child) => {
      const pad = { paddingLeft: 8 + depth * 14 };
      if (child.isFile) {
        const flag = flags[child.path];
        return (
          <div
            key={child.path}
            className={`tree-row${selected === child.path ? " sel" : ""}`}
            style={pad}
            onClick={() => onSelect(child.path)}
            title={child.path}
          >
            <span className="caret" />
            <span className="name">{child.name}</span>
            {flag && <span className={`flag ${flag}`} title={flag === "finding" ? "Involved in a finding" : "Changed by the plan"} />}
          </div>
        );
      }
      const open = isOpen(child);
      return (
        <div key={child.path}>
          <div className="tree-row" style={pad} onClick={() => toggle(child)}>
            <span className="caret">{open ? "▾" : "▸"}</span>
            <span className="name">{child.name}/</span>
          </div>
          {open && render(child, depth + 1)}
        </div>
      );
    });

  if (files.length === 0) return <div className="empty">No files</div>;
  return <div className="tree">{render(tree, 0)}</div>;
}
