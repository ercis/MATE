"use client";

import Link from "next/link";
import { useProgressRouter } from "@/lib/use-progress-router";
import { useQueryClient } from "@tanstack/react-query";
import { prefetchEventLog } from "@/lib/client-prefetch";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Copy,
  FolderClosed,
  FolderOpen,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  RefreshCcw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  closestCenter,
  DndContext,
  DragOverlay,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { toastError } from "@/lib/toast";
import { cn } from "@/lib/cn";
import { formatDateRange, formatNumber, formatRelative } from "@/lib/format";
import type { EventLogSummary, FolderSummary, ReorderItem } from "@/lib/api-types";
import {
  useCreateFolder,
  useDeleteEventLog,
  useDeleteFolder,
  useDuplicateEventLog,
  useFolders,
  useReimportEventLog,
  useRenameEventLog,
  useRenameFolder,
  useReorderTree,
} from "@/lib/queries";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";

import { FormatBadge } from "./format-badge";

// ── Types ─────────────────────────────────────────────────────────────────────

type NodeId = string; // `folder:<id>` or `log:<id>`

interface FolderNode {
  kind: "folder";
  id: string;
  folder: FolderSummary;
  parentId: string | null;
  children: TreeNode[];
}
interface LogNode {
  kind: "log";
  id: string;
  log: EventLogSummary;
  parentId: string | null;
}
type TreeNode = FolderNode | LogNode;

interface FlatRow {
  node: TreeNode;
  depth: number;
}

function nodeId(node: TreeNode): NodeId {
  return `${node.kind}:${node.id}`;
}

// ── Tree building ─────────────────────────────────────────────────────────────

function buildTree(
  folders: FolderSummary[],
  logs: EventLogSummary[],
): TreeNode[] {
  const folderChildren = new Map<string | null, FolderSummary[]>();
  for (const f of folders) {
    const key = f.parent_id ?? null;
    const list = folderChildren.get(key) ?? [];
    list.push(f);
    folderChildren.set(key, list);
  }
  for (const list of folderChildren.values()) {
    list.sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
  }

  const logChildren = new Map<string | null, EventLogSummary[]>();
  for (const l of logs) {
    const key = l.folder_id ?? null;
    const list = logChildren.get(key) ?? [];
    list.push(l);
    logChildren.set(key, list);
  }
  for (const list of logChildren.values()) {
    list.sort((a, b) => a.position - b.position || b.created_at.localeCompare(a.created_at));
  }

  // Folders first, then logs – each sorted by position within their parent.
  const build = (parentId: string | null): TreeNode[] => {
    const out: TreeNode[] = [];
    for (const f of folderChildren.get(parentId) ?? []) {
      out.push({
        kind: "folder",
        id: f.id,
        folder: f,
        parentId,
        children: build(f.id),
      });
    }
    for (const l of logChildren.get(parentId) ?? []) {
      out.push({ kind: "log", id: l.id, log: l, parentId });
    }
    return out;
  };
  return build(null);
}

function flatten(tree: TreeNode[], expanded: Set<string>, depth = 0): FlatRow[] {
  const out: FlatRow[] = [];
  for (const node of tree) {
    out.push({ node, depth });
    if (node.kind === "folder" && expanded.has(node.id)) {
      out.push(...flatten(node.children, expanded, depth + 1));
    }
  }
  return out;
}

// ── Top-level component ───────────────────────────────────────────────────────

interface ProcessesTableProps {
  rows: EventLogSummary[];
}

export function ProcessesTable({ rows }: ProcessesTableProps) {
  const foldersQ = useFolders();
  // Keep a stable reference: `?? []` would allocate a fresh array every render
  // while the query is loading, which churns every downstream memo/effect that
  // depends on `folders` (notably the auto-expand effect below → render loop).
  const folders = useMemo(() => foldersQ.data ?? [], [foldersQ.data]);
  const tree = useMemo(() => buildTree(folders, rows), [folders, rows]);

  const [expanded, setExpanded] = useState<Set<string>>(() => {
    // Start with everything expanded – folders are usually shallow.
    return new Set(folders.map((f) => f.id));
  });
  // Auto-expand newly created folders. Returns `prev` unchanged when nothing
  // was added – otherwise this would emit a fresh Set on every render and, with
  // `folders` being a new `[]` reference while the query loads, spin into an
  // infinite render loop (React error #185).
  useEffect(() => {
    setExpanded((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const f of folders) {
        if (!next.has(f.id)) {
          next.add(f.id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [folders]);

  const reorder = useReorderTree();
  const [activeId, setActiveId] = useState<NodeId | null>(null);
  const [dropTargetFolderId, setDropTargetFolderId] = useState<string | null>(null);

  const sensors = useSensors(
    // 5px tolerance lets simple clicks pass through to the row's open-on-click.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const flat = useMemo(() => flatten(tree, expanded), [tree, expanded]);
  const sortableIds = useMemo(() => flat.map((r) => nodeId(r.node)), [flat]);

  // Lookup helpers built from the current tree.
  const byId = useMemo(() => {
    const m = new Map<NodeId, TreeNode>();
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        m.set(nodeId(n), n);
        if (n.kind === "folder") walk(n.children);
      }
    };
    walk(tree);
    return m;
  }, [tree]);

  const onDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as NodeId);
  };

  const onDragOver = (event: DragOverEvent) => {
    const over = event.over;
    if (!over) {
      setDropTargetFolderId(null);
      return;
    }
    const overId = over.id as string;
    // Only highlight when the drop target is the "into folder" zone.
    if (overId.startsWith("into:folder:")) {
      setDropTargetFolderId(overId.slice("into:folder:".length));
    } else {
      setDropTargetFolderId(null);
    }
  };

  const onDragEnd = (event: DragEndEvent) => {
    setActiveId(null);
    setDropTargetFolderId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const activeNode = byId.get(active.id as NodeId);
    if (!activeNode) return;

    const overIdStr = over.id as string;

    // Case A: dropped on a folder's body → move into that folder (at end).
    if (overIdStr.startsWith("into:folder:")) {
      const targetFolderId = overIdStr.slice("into:folder:".length);
      if (activeNode.kind === "folder" && activeNode.id === targetFolderId) return;
      void moveIntoFolder(activeNode, targetFolderId);
      return;
    }

    // Case B: dropped on a sibling row → reorder within that row's parent.
    if (overIdStr.startsWith("folder:") || overIdStr.startsWith("log:")) {
      const overNode = byId.get(overIdStr as NodeId);
      if (!overNode) return;
      void moveAdjacent(activeNode, overNode);
      return;
    }

    // Case C: dropped on the root droppable → move to root, append.
    if (overIdStr === "into:root") {
      void moveIntoFolder(activeNode, null);
    }
  };

  /** Move a node into the end of `targetFolderId` (null = root). */
  const moveIntoFolder = async (node: TreeNode, targetFolderId: string | null) => {
    // Determine current siblings at the destination (same kind), then append.
    const siblings = collectSiblings(tree, targetFolderId, node.kind).filter(
      (n) => !(n.kind === node.kind && n.id === node.id),
    );
    const items: ReorderItem[] = [
      ...siblings.map((n, i) => ({
        kind: n.kind,
        id: n.id,
        parent_id: targetFolderId,
        position: i,
      })),
      {
        kind: node.kind,
        id: node.id,
        parent_id: targetFolderId,
        position: siblings.length,
      },
    ];
    try {
      await reorder.mutateAsync(items);
    } catch (err) {
      toastError(`Move failed: ${(err as Error).message}`);
    }
  };

  /** Place `active` adjacent to `over` within `over`'s parent + kind list. */
  const moveAdjacent = async (active: TreeNode, over: TreeNode) => {
    if (active.kind !== over.kind) {
      // Can't reorder a folder into the log list or vice-versa via DnD;
      // user should use the "Move to…" context menu for cross-kind moves.
      return;
    }
    const newParent = over.parentId;
    const siblings = collectSiblings(tree, newParent, active.kind).filter(
      (n) => n.id !== active.id,
    );
    const overIdx = siblings.findIndex((n) => n.id === over.id);
    if (overIdx === -1) return;

    // Insert active at overIdx (matches the "drop before" intent of dnd-kit's
    // closestCenter when the item lands just above `over`).
    const reordered = [...siblings];
    reordered.splice(overIdx, 0, active);
    const items: ReorderItem[] = reordered.map((n, i) => ({
      kind: n.kind,
      id: n.id,
      parent_id: newParent,
      position: i,
    }));
    try {
      await reorder.mutateAsync(items);
    } catch (err) {
      toastError(`Reorder failed: ${(err as Error).message}`);
    }
  };

  const toggleExpanded = (folderId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  };

  const activeNode = activeId ? byId.get(activeId) : null;

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
        <div className="divide-y divide-border">
          <HeaderRow />
          {flat.map((row) => (
            <TreeRowDispatch
              key={nodeId(row.node)}
              row={row}
              folders={folders}
              expanded={expanded}
              toggleExpanded={toggleExpanded}
              isDropTarget={
                row.node.kind === "folder" && dropTargetFolderId === row.node.id
              }
            />
          ))}
          {flat.length === 0 && (
            <div className="p-6 text-center text-sm text-muted-foreground">
              No items yet - import an event log to get started.
            </div>
          )}
          <RootDropZone visible={Boolean(activeId)} />
        </div>
      </SortableContext>

      <DragOverlay>
        {activeNode ? <DragPreview node={activeNode} /> : null}
      </DragOverlay>
    </DndContext>
  );
}

function collectSiblings(
  tree: TreeNode[],
  parentId: string | null,
  kind: "folder" | "log",
): TreeNode[] {
  if (parentId === null) {
    return tree.filter((n) => n.kind === kind);
  }
  // BFS to find the parent folder.
  const stack: TreeNode[] = [...tree];
  while (stack.length) {
    const n = stack.shift()!;
    if (n.kind === "folder") {
      if (n.id === parentId) return n.children.filter((c) => c.kind === kind);
      stack.push(...n.children);
    }
  }
  return [];
}

// ── Header ────────────────────────────────────────────────────────────────────

function HeaderRow() {
  return (
    <div className="grid grid-cols-[1fr_70px_90px_80px_180px_120px_60px_40px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground">
      <div>Name</div>
      <div className="text-right">Cases</div>
      <div className="text-right">Events</div>
      <div className="text-right">Variants</div>
      <div>Date range</div>
      <div>Imported</div>
      <div>Format</div>
      <div />
    </div>
  );
}

// ── Row dispatcher ────────────────────────────────────────────────────────────

function TreeRowDispatch({
  row,
  folders,
  expanded,
  toggleExpanded,
  isDropTarget,
}: {
  row: FlatRow;
  folders: FolderSummary[];
  expanded: Set<string>;
  toggleExpanded: (id: string) => void;
  isDropTarget: boolean;
}) {
  if (row.node.kind === "folder") {
    return (
      <FolderRow
        node={row.node}
        depth={row.depth}
        folders={folders}
        isExpanded={expanded.has(row.node.id)}
        toggleExpanded={toggleExpanded}
        isDropTarget={isDropTarget}
      />
    );
  }
  return <LogRow node={row.node} depth={row.depth} folders={folders} />;
}

// ── Folder row ────────────────────────────────────────────────────────────────

function FolderRow({
  node,
  depth,
  folders,
  isExpanded,
  toggleExpanded,
  isDropTarget,
}: {
  node: FolderNode;
  depth: number;
  folders: FolderSummary[];
  isExpanded: boolean;
  toggleExpanded: (id: string) => void;
  isDropTarget: boolean;
}) {
  const id = nodeId(node);
  const sortable = useSortable({ id });
  const dropInto = useDroppable({ id: `into:folder:${node.id}` });

  const style = {
    transform: CSS.Transform.toString(sortable.transform),
    transition: sortable.transition,
  };

  const [renameOpen, setRenameOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [newSubOpen, setNewSubOpen] = useState(false);
  const rename = useRenameFolder();
  const del = useDeleteFolder();

  const Icon = isExpanded ? FolderOpen : FolderClosed;

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div
            ref={(el) => {
              sortable.setNodeRef(el);
              dropInto.setNodeRef(el);
            }}
            style={style}
            {...sortable.attributes}
            {...sortable.listeners}
            data-folder-id={node.id}
            className={cn(
              "grid grid-cols-[1fr_70px_90px_80px_180px_120px_60px_40px] gap-2 items-center px-4 py-2 text-sm bg-muted/30 hover:bg-muted/50 cursor-grab active:cursor-grabbing",
              sortable.isDragging && "opacity-30",
              isDropTarget && "ring-2 ring-primary ring-inset bg-primary/5",
            )}
            onClick={(e) => {
              // Toggle expansion when clicking row body (not menus / chevron).
              if ((e.target as HTMLElement).closest("[data-row-stop]")) return;
              toggleExpanded(node.id);
            }}
          >
            <div className="flex items-center gap-1 min-w-0" style={{ paddingLeft: depth * 16 }}>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  toggleExpanded(node.id);
                }}
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-muted-foreground/10 cursor-pointer"
                data-row-stop
                aria-label={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate font-medium">{node.folder.name}</span>
              <span className="ml-2 text-[10px] text-muted-foreground">
                {countDescendantLogs(node)} {countDescendantLogs(node) === 1 ? "log" : "logs"}
              </span>
            </div>
            <div />
            <div />
            <div />
            <div />
            <div />
            <div />
            <div data-row-stop onClick={(e) => e.stopPropagation()}>
              <FolderActionsDropdown
                onRename={() => setRenameOpen(true)}
                onNewSubfolder={() => setNewSubOpen(true)}
                onDelete={() => setConfirmDeleteOpen(true)}
              />
            </div>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem onSelect={() => toggleExpanded(node.id)}>
            {isExpanded ? "Collapse" : "Expand"}
          </ContextMenuItem>
          <ContextMenuItem onSelect={() => setNewSubOpen(true)}>
            <FolderPlus className="mr-2 h-3.5 w-3.5" />
            New subfolder
          </ContextMenuItem>
          <ContextMenuItem onSelect={() => setRenameOpen(true)}>
            <Pencil className="mr-2 h-3.5 w-3.5" />
            Rename
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem
            variant="destructive"
            onSelect={() => setConfirmDeleteOpen(true)}
          >
            <Trash2 className="mr-2 h-3.5 w-3.5" />
            Delete folder…
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>

      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        title="Rename folder"
        currentName={node.folder.name}
        pending={rename.isPending}
        onConfirm={async (next) => {
          try {
            await rename.mutateAsync({ id: node.id, name: next });
            toast.success(`Renamed folder to "${next}"`);
            setRenameOpen(false);
          } catch (err) {
            toastError(`Rename failed: ${(err as Error).message}`);
          }
        }}
      />

      <NewFolderDialog
        open={newSubOpen}
        onOpenChange={setNewSubOpen}
        parentId={node.id}
        parentName={node.folder.name}
      />

      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{node.folder.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              {(() => {
                const logCount = countDescendantLogs(node);
                const folderCount = countDescendantFolders(node);
                const parts: string[] = [];
                if (logCount > 0) {
                  parts.push(`${logCount} event ${logCount === 1 ? "log" : "logs"}`);
                }
                if (folderCount > 0) {
                  parts.push(`${folderCount} ${folderCount === 1 ? "subfolder" : "subfolders"}`);
                }
                if (parts.length === 0) {
                  return "The folder will be permanently removed. This cannot be undone.";
                }
                return `This will permanently delete the folder, ${parts.join(" and ")}, including all Parquet files and original uploads on disk. This cannot be undone.`;
              })()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                try {
                  await del.mutateAsync(node.id);
                  toast.success(`Deleted folder "${node.folder.name}"`);
                } catch (err) {
                  toastError(`Delete failed: ${(err as Error).message}`);
                }
              }}
              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function countDescendantLogs(node: FolderNode): number {
  let count = 0;
  const walk = (n: TreeNode) => {
    if (n.kind === "log") count++;
    else for (const c of n.children) walk(c);
  };
  for (const c of node.children) walk(c);
  return count;
}

function countDescendantFolders(node: FolderNode): number {
  let count = 0;
  const walk = (n: TreeNode) => {
    if (n.kind !== "folder") return;
    count++;
    for (const c of n.children) walk(c);
  };
  for (const c of node.children) walk(c);
  return count;
}

function FolderActionsDropdown({
  onRename,
  onNewSubfolder,
  onDelete,
}: {
  onRename: () => void;
  onNewSubfolder: () => void;
  onDelete: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-7 w-7 cursor-pointer">
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onNewSubfolder} className="cursor-pointer">
          <FolderPlus className="mr-2 h-3.5 w-3.5" />
          New subfolder
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onRename} className="cursor-pointer">
          <Pencil className="mr-2 h-3.5 w-3.5" />
          Rename
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={onDelete}
          className="cursor-pointer text-destructive focus:text-destructive"
        >
          <Trash2 className="mr-2 h-3.5 w-3.5" />
          Delete folder…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ── Log row ───────────────────────────────────────────────────────────────────

function LogRow({
  node,
  depth,
  folders,
}: {
  node: LogNode;
  depth: number;
  folders: FolderSummary[];
}) {
  const row = node.log;
  const id = nodeId(node);
  const sortable = useSortable({ id });

  const router = useProgressRouter();
  const qc = useQueryClient();
  const importing = row.status === "importing";
  // Parsed but held until every subscribing module finishes precomputing. Shares
  // the importing visuals (dimmed row + indeterminate bar) but with its own
  // label; opening stays gated on `ready`.
  const processing = row.status === "processing";
  const failed = row.status === "failed";
  const ready = row.status === "ready";
  const busy = importing || processing;

  const del = useDeleteEventLog();
  const rename = useRenameEventLog();
  const reimport = useReimportEventLog();
  const duplicate = useDuplicateEventLog();
  const reorder = useReorderTree();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [reimportOpen, setReimportOpen] = useState(false);

  const onOpen = () => {
    if (!ready) return;
    router.push(`/processes/${row.id}`);
  };

  const onDelete = async () => {
    try {
      await del.mutateAsync(row.id);
      toast.success(`Deleted "${row.name}"`);
    } catch (err) {
      toastError(`Delete failed: ${(err as Error).message}`);
    }
  };

  const onReimport = async () => {
    try {
      await reimport.mutateAsync(row.id);
      toast.success(`Re-importing "${row.name}"`);
    } catch (err) {
      toastError(`Re-import failed: ${(err as Error).message}`);
    }
  };

  const onDuplicate = async () => {
    try {
      const dup = await duplicate.mutateAsync(row.id);
      toast.success(`Duplicated as "${dup.name}"`);
    } catch (err) {
      toastError(`Duplicate failed: ${(err as Error).message}`);
    }
  };

  const onMoveTo = async (folderId: string | null) => {
    try {
      // Reuse the reorder endpoint: append to the destination at the end.
      // The backend will accept this single-item payload and update position.
      await reorder.mutateAsync([
        {
          kind: "log",
          id: row.id,
          parent_id: folderId,
          // Push to a high position so the backend places it last – the next
          // listing fetch will normalise it relative to existing siblings.
          position: 999999,
        },
      ]);
      const dest = folderId
        ? folders.find((f) => f.id === folderId)?.name ?? "folder"
        : "Root";
      toast.success(`Moved "${row.name}" to ${dest}`);
    } catch (err) {
      toastError(`Move failed: ${(err as Error).message}`);
    }
  };

  const style = {
    transform: CSS.Transform.toString(sortable.transform),
    transition: sortable.transition,
  };

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div
            ref={sortable.setNodeRef}
            style={style}
            {...sortable.attributes}
            {...sortable.listeners}
            className={cn(
              "grid grid-cols-[1fr_70px_90px_80px_180px_120px_60px_40px] gap-2 items-center px-4 py-2 text-sm hover:bg-accent/50",
              ready && "cursor-pointer",
              busy && "opacity-60",
              sortable.isDragging && "opacity-30",
            )}
            onMouseEnter={() => {
              // Warm the detail cache so opening the log is instant.
              if (ready) prefetchEventLog(qc, row.id);
            }}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("[data-row-stop]")) return;
              if (ready) onOpen();
            }}
          >
            <div className="min-w-0" style={{ paddingLeft: depth * 16 + 24 }}>
              <div className="truncate font-medium">{row.name}</div>
              {busy && (
                <div className="mt-1 max-w-xs">
                  <Progress value={undefined} className="h-1" />
                  {processing && (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Preparing modules…
                    </div>
                  )}
                </div>
              )}
              {failed && (
                <HoverCard>
                  <HoverCardTrigger asChild>
                    <div className="mt-1 flex cursor-default items-center gap-1">
                      <AlertCircle className="h-3 w-3 shrink-0 text-destructive" />
                      <span className="truncate text-xs text-destructive">
                        {row.error ?? "Import failed"}
                      </span>
                    </div>
                  </HoverCardTrigger>
                  {row.error && (
                    <HoverCardContent side="bottom" align="start" className="w-96">
                      <pre className="whitespace-pre-wrap break-words text-[11px] text-destructive">
                        {row.error}
                      </pre>
                    </HoverCardContent>
                  )}
                </HoverCard>
              )}
              {ready && row.mapping_needs_review && (
                <Link
                  href={`/processes/${row.id}?tab=settings`}
                  data-row-stop
                  onClick={(e) => e.stopPropagation()}
                  className="mt-1 flex w-fit items-center gap-1 text-amber-600 hover:underline dark:text-amber-500"
                  title="The importer guessed one or more mandatory columns. Open settings to confirm the mapping."
                >
                  <AlertTriangle className="h-3 w-3 shrink-0" />
                  <span className="truncate text-xs">Review column mapping</span>
                </Link>
              )}
            </div>
            <div className="text-right tabular-nums">{formatNumber(row.cases_count)}</div>
            <div className="text-right tabular-nums">{formatNumber(row.events_count)}</div>
            <div className="text-right tabular-nums">{formatNumber(row.variants_count)}</div>
            <div className="text-xs text-muted-foreground truncate">
              {formatDateRange(row.date_min, row.date_max)}
            </div>
            <div className="text-xs text-muted-foreground truncate">
              <span title={row.imported_at ?? row.created_at}>
                {formatRelative(row.imported_at ?? row.created_at)}
              </span>
            </div>
            <div>
              <FormatBadge format={row.source_format} />
            </div>
            <div data-row-stop onClick={(e) => e.stopPropagation()}>
              <LogActionsDropdown
                disabled={busy}
                ready={ready}
                hasSourceFormat={Boolean(row.source_format)}
                folders={folders}
                currentFolderId={row.folder_id}
                onOpen={onOpen}
                onRename={() => setRenameOpen(true)}
                onDuplicate={onDuplicate}
                onReimport={() => setReimportOpen(true)}
                onMoveTo={onMoveTo}
                onDelete={() => setConfirmOpen(true)}
              />
            </div>
          </div>
        </ContextMenuTrigger>

        <ContextMenuContent>
          <ContextMenuItem disabled={busy} onSelect={() => setRenameOpen(true)}>
            <Pencil className="mr-2 h-3.5 w-3.5" />
            Rename
          </ContextMenuItem>
          <ContextMenuItem disabled={!ready} onSelect={onDuplicate}>
            <Copy className="mr-2 h-3.5 w-3.5" />
            Duplicate
          </ContextMenuItem>
          <ContextMenuItem
            disabled={busy || !row.source_format}
            onSelect={() => setReimportOpen(true)}
          >
            <RefreshCcw className="mr-2 h-3.5 w-3.5" />
            Re-run import
          </ContextMenuItem>
          <ContextMenuSub>
            <ContextMenuSubTrigger disabled={busy}>
              <FolderClosed className="mr-2 h-3.5 w-3.5" />
              Move to
            </ContextMenuSubTrigger>
            <ContextMenuSubContent>
              <ContextMenuItem
                disabled={row.folder_id === null}
                onSelect={() => onMoveTo(null)}
              >
                Root
              </ContextMenuItem>
              {folders.length > 0 && <ContextMenuSeparator />}
              {folders.map((f) => (
                <ContextMenuItem
                  key={f.id}
                  disabled={f.id === row.folder_id}
                  onSelect={() => onMoveTo(f.id)}
                >
                  {f.name}
                </ContextMenuItem>
              ))}
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSeparator />
          <ContextMenuItem
            variant="destructive"
            onSelect={() => setConfirmOpen(true)}
          >
            <Trash2 className="mr-2 h-3.5 w-3.5" />
            Delete…
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>

      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        title="Rename event log"
        currentName={row.name}
        pending={rename.isPending}
        onConfirm={async (next) => {
          try {
            await rename.mutateAsync({ id: row.id, name: next });
            toast.success(`Renamed to "${next}"`);
            setRenameOpen(false);
          } catch (err) {
            toastError(`Rename failed: ${(err as Error).message}`);
          }
        }}
      />

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{row.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              The Parquet files and the original upload will be removed from
              disk. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={reimportOpen} onOpenChange={setReimportOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Re-run import for &ldquo;{row.name}&rdquo;?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The original upload on disk is re-parsed from scratch. The log
              will be marked <em>importing</em> and unavailable to open until
              the new import finishes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onReimport}
              className="cursor-pointer"
              disabled={reimport.isPending}
            >
              Re-run import
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function LogActionsDropdown({
  disabled,
  ready,
  hasSourceFormat,
  folders,
  currentFolderId,
  onOpen,
  onRename,
  onDuplicate,
  onReimport,
  onMoveTo,
  onDelete,
}: {
  disabled: boolean;
  ready: boolean;
  hasSourceFormat: boolean;
  folders: FolderSummary[];
  currentFolderId: string | null;
  onOpen: () => void;
  onRename: () => void;
  onDuplicate: () => void;
  onReimport: () => void;
  onMoveTo: (folderId: string | null) => void;
  onDelete: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="h-7 w-7 cursor-pointer">
          <MoreHorizontal className="h-4 w-4" />
          <span className="sr-only">Row actions</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem disabled={!ready} onSelect={onOpen} className="cursor-pointer">
          Open
        </DropdownMenuItem>
        <DropdownMenuItem disabled={disabled} onSelect={onRename} className="cursor-pointer">
          <Pencil className="mr-2 h-3.5 w-3.5" />
          Rename
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!ready} onSelect={onDuplicate} className="cursor-pointer">
          <Copy className="mr-2 h-3.5 w-3.5" />
          Duplicate
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={disabled || !hasSourceFormat}
          onSelect={onReimport}
          className="cursor-pointer"
        >
          <RefreshCcw className="mr-2 h-3.5 w-3.5" />
          Re-run import
        </DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={disabled} className="cursor-pointer">
            <FolderClosed className="mr-2 h-3.5 w-3.5" />
            Move to
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuItem
              disabled={currentFolderId === null}
              onSelect={() => onMoveTo(null)}
              className="cursor-pointer"
            >
              Root
            </DropdownMenuItem>
            {folders.length > 0 && <DropdownMenuSeparator />}
            {folders.map((f) => (
              <DropdownMenuItem
                key={f.id}
                disabled={f.id === currentFolderId}
                onSelect={() => onMoveTo(f.id)}
                className="cursor-pointer"
              >
                {f.name}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={onDelete}
          className="cursor-pointer text-destructive focus:text-destructive"
        >
          <Trash2 className="mr-2 h-3.5 w-3.5" />
          Delete…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ── Drag overlay + root drop zone ─────────────────────────────────────────────

function DragPreview({ node }: { node: TreeNode }) {
  return (
    <div className="flex items-center gap-2 rounded-md border bg-popover px-3 py-2 text-sm shadow-lg">
      {node.kind === "folder" ? (
        <FolderClosed className="h-4 w-4 text-muted-foreground" />
      ) : null}
      <span className="font-medium">
        {node.kind === "folder" ? node.folder.name : node.log.name}
      </span>
    </div>
  );
}

function RootDropZone({ visible }: { visible: boolean }) {
  const drop = useDroppable({ id: "into:root" });
  if (!visible) return null;
  return (
    <div
      ref={drop.setNodeRef}
      className={cn(
        "border-2 border-dashed border-muted-foreground/30 m-2 rounded-md p-3 text-center text-xs text-muted-foreground transition-colors",
        drop.isOver && "border-primary bg-primary/5 text-primary",
      )}
    >
      Drop here to move to root
    </div>
  );
}

// ── Dialogs ───────────────────────────────────────────────────────────────────

function RenameDialog({
  open,
  onOpenChange,
  title,
  currentName,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  currentName: string;
  pending: boolean;
  onConfirm: (name: string) => void | Promise<void>;
}) {
  const [name, setName] = useState(currentName);
  useEffect(() => {
    if (open) setName(currentName);
  }, [open, currentName]);

  const trimmed = name.trim();
  const canSave = trimmed.length > 0 && trimmed !== currentName.trim() && !pending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>The display name shown across the platform.</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canSave) onConfirm(trimmed);
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="rename-input" className="text-xs text-muted-foreground">
              Name
            </Label>
            <Input
              id="rename-input"
              autoFocus
              maxLength={255}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="cursor-pointer"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" className="cursor-pointer" disabled={!canSave}>
              {pending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function NewFolderDialog({
  open,
  onOpenChange,
  parentId = null,
  parentName,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  parentId?: string | null;
  parentName?: string;
}) {
  const [name, setName] = useState("");
  const create = useCreateFolder();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName("");
      // The Dialog's focus-trap settles first; give it a tick.
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  const trimmed = name.trim();
  const canSave = trimmed.length > 0 && !create.isPending;

  const onSubmit = async () => {
    try {
      await create.mutateAsync({ name: trimmed, parent_id: parentId });
      toast.success(`Created folder "${trimmed}"`);
      onOpenChange(false);
    } catch (err) {
      toastError(`Create failed: ${(err as Error).message}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New folder</DialogTitle>
          <DialogDescription>
            {parentName ? `Created inside "${parentName}".` : "Created at root."}
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canSave) void onSubmit();
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="new-folder-input" className="text-xs text-muted-foreground">
              Name
            </Label>
            <Input
              ref={inputRef}
              id="new-folder-input"
              maxLength={255}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Q1 2026"
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="cursor-pointer"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" className="cursor-pointer" disabled={!canSave}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// Helper used by the parent page when there's no logId in the URL.
export function processesHref(id: string) {
  return `/processes/${id}` as const;
}
