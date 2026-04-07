import { createBrowserRouter } from "react-router";

import { listRooms, getRoomSnapshot } from "../lib/api";
import { HomePage } from "../routes/home";
import { ResultsPage } from "../routes/results";
import { RootHydrateFallback, RootLayout } from "../routes/root";
import { RoomPage } from "../routes/room";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    HydrateFallback: RootHydrateFallback,
    children: [
      {
        index: true,
        loader: async () => listRooms(),
        Component: HomePage,
      },
      {
        path: "rooms/:roomId",
        loader: async ({ params }) => getRoomSnapshot(params.roomId ?? ""),
        Component: RoomPage,
      },
      {
        path: "rooms/:roomId/results",
        loader: async ({ params }) => getRoomSnapshot(params.roomId ?? ""),
        Component: ResultsPage,
      },
    ],
  },
]);
