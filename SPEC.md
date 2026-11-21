
# Spec

Yee 3 should open folders containing tens of thousands of images quickly and keep image-to-image navigation responsive, including on high-latency storage such as NAS.
Navigation and order switching remain available during incremental loading.

## Folder Scans

Each scan completes after one directory pass and makes discovered images available for browsing as it progresses.
Changes detected during a scan prompt the user to reload manually. They never restart the scan or schedule an automatic follow-up.
A change notification received after completion may start a new scan when no manual reload is pending.

## Order Model

A folder is one collection of image records (`ImageData`) with three maintained orders.

Base order sets:
- `mtimeOrderSet`: newest-first order by file modified time
- `fnameOrderSet`: descending lexical order by file name
- `randomOrderSet`: stable pseudo-random order

Active navigation bindings:
- `verticalOrderSet`: the order currently assigned to vertical navigation
- `horizontalOrderSet`: the order currently assigned to horizontal navigation

Each axis uses its selected order independently. Equal sort keys are resolved by path.
Up and left move toward the end of the selected order. Down and right move toward its start.
Arrow keys and scrolling use the same directions for image navigation. When the zoomed image can pan along the scroll axis, scrolling pans within the image.

```mermaid
flowchart LR
    A["Discovered ImageData batch"] -->|"apply batch"| B["mtimeOrderSet"]
    A -->|"apply batch"| C["fnameOrderSet"]
    A -->|"apply batch"| D["randomOrderSet"]

    B -.->|"select as current"| E["verticalOrderSet"]
    C -.->|"select as current"| E
    D -.->|"select as current"| E

    B -.->|"select as current"| F["horizontalOrderSet"]
    C -.->|"select as current"| F
    D -.->|"select as current"| F

    E -->|"next/prev path from currentPath"| G["Vertical navigation"]
    F -->|"next/prev path from currentPath"| H["Horizontal navigation"]

    B --->|"index_map[path_nf]"| I["current image lookup"]
    C --->|"index_map[path_nf]"| I
    D --->|"index_map[path_nf]"| I

    I -->|"resolve current image"| G
    I -->|"resolve current image"| H
```

## Current Image State

- `currentPath`: the identity of the currently displayed image
- `currentPixmap`: the decoded base image used for rendering and scale calculations

`loadImageFromFile(imageData)` updates both values after decoding succeeds.

```mermaid
flowchart LR
    A["loadImageFromFile(imageData)"] -->|"imageData.path_nf"| B["currentPath"]
    A ---->|"decoded image"| C["currentPixmap"]
    C --->|"render + fit"| D["imageDisplay"]

    B -->|"resolve current position"| E["Vertical navigation"]
    B -->|"resolve current position"| F["Horizontal navigation"]

    E -->|"loadImageFromFile(nextImage)"| A
    F -->|"loadImageFromFile(nextImage)"| A
```

## Complexity Constraints

Let `N` be the number of indexed images and `D` the number of directory entries.

- A directory pass examines `O(D)` entries and reads metadata for image candidates.
- Building an order incrementally is currently worst-case `O(N²)` because array insertion can shift existing entries. The target is `O(N log N)` for construction.
- The final removal pass in a successful rescan filters and relinks each order in `O(N)`.
- Looking up a path and following its previous or next link take average `O(1)` in the order index. These bounds cover index operations. Image I/O and decoding have their own costs.
- Switching either axis to a different order takes `O(1)`.
