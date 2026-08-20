// Type shim for react-grid-layout v1.5.x, which ships no own declarations and
// whose `@types/react-grid-layout` package is a misleading deprecation stub.
// Covers only the surface the Dashboards canvas uses.

declare module "react-grid-layout" {
  import type * as React from "react";

  export interface Layout {
    i: string;
    x: number;
    y: number;
    w: number;
    h: number;
    minW?: number;
    maxW?: number;
    minH?: number;
    maxH?: number;
    static?: boolean;
    isDraggable?: boolean;
    isResizable?: boolean;
  }

  export type LayoutItem = Layout;

  export interface ReactGridLayoutProps {
    className?: string;
    layout?: Layout[];
    cols?: number;
    rowHeight?: number;
    width?: number;
    margin?: [number, number];
    containerPadding?: [number, number];
    isDraggable?: boolean;
    isResizable?: boolean;
    isDroppable?: boolean;
    draggableHandle?: string;
    compactType?: "vertical" | "horizontal" | null;
    droppingItem?: { i: string; w: number; h: number };
    onLayoutChange?: (layout: Layout[]) => void;
    // `item` is undefined when RGL tears down its dropping placeholder before the
    // drop lands (its HTML5 droppable is racy) — callers must tolerate it.
    onDrop?: (layout: Layout[], item: Layout | undefined, event: Event) => void;
    children?: React.ReactNode;
  }

  export default class ReactGridLayout extends React.Component<ReactGridLayoutProps> {}

  export class Responsive extends React.Component<ReactGridLayoutProps> {}

  export function WidthProvider<P>(
    component: React.ComponentType<P>,
  ): React.ComponentType<Omit<P, "width">>;
}
