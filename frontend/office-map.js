/**
 * office-map.js — Tilemap data layer for the office renderer.
 *
 * Converts the backend layout API response + employee/room/tool lists into
 * a 2D grid of tile keys and entity position lists.
 *
 * Coordinate system:
 *   Backend uses (gx, gy) where gy=0 is the executive row in the office area.
 *   Canvas rows add WALL_ROWS (3) offset: canvas_row = gy + WALL_ROWS.
 *   OfficeMap stores all entity positions in canvas-row space.
 *   Callers MUST NOT add +3 again.
 */

const WALL_ROWS = 3;   // rows reserved for wall/header at top of canvas
const MAP_COLS  = 20;  // fixed grid width (matches backend COLS=20)

// Founding employee IDs
const CEO_ID   = '00001';
const EXEC_IDS = new Set(['00002', '00003', '00004', '00005']);
const EXEC_ROLES = {
  '00001': 'CEO',
  '00002': 'HR',
  '00003': 'COO',
  '00004': 'EA',
  '00005': 'CSO',
};

class OfficeMap {
  constructor() {
    this.cols = MAP_COLS;
    this.rows = 18;  // default, updated from layout API

    // Flat typed arrays indexed [row * cols + col]
    this._floor    = [];  // floor tile def key per cell
    this._overlay  = [];  // decoration tile def key (or null)
    this._isDivider = []; // boolean: plant divider column

    // Zone metadata (from layout API)
    this.zones      = [];
    this.dividerCols = new Set();

    // Entity lists (canvas-space coordinates)
    this.employees    = [];   // regular employees
    this.executives   = [];   // founding employees (excluding CEO)
    this.ceo          = null; // CEO entity
    this.meetingRooms = [];
    this.tools        = [];
  }

  /**
   * Rebuild the entire tilemap from fresh API data.
   * @param {object} layoutData  — office_layout from state API
   * @param {Array}  employees   — from /api/employees
   * @param {Array}  rooms       — from /api/rooms (or meeting_rooms from state)
   * @param {Array}  tools       — from /api/tools
   */
  rebuild(layoutData, employees, rooms, tools) {
    this.rows = layoutData.canvas_rows || 18;
    this.cols = MAP_COLS;
    this.zones = layoutData.zones || [];
    this.dividerCols = new Set(layoutData.divider_cols || []);

    const size = this.rows * this.cols;
    this._floor    = new Array(size).fill('floor_stone_gray');
    this._overlay  = new Array(size).fill(null);
    this._isDivider = new Array(size).fill(false);

    this._buildFloors(layoutData);
    this._buildDividers();
    this._buildEntities(employees || [], rooms || [], tools || [], layoutData);
  }

  _buildFloors(layoutData) {
    const execGY  = layoutData.executive_row ?? 0;
    const execH   = layoutData.exec_row_height ?? 2;

    // Executive area (gold wood floor)
    for (let r = 0; r < execH; r++) {
      const canvasRow = execGY + r + WALL_ROWS;
      for (let col = 0; col < this.cols; col++) {
        this._setFloor(col, canvasRow, 'floor_wood_gold');
      }
    }

    // Department zones
    const deptStartGY = layoutData.dept_start_row ?? 4;
    const deptEndGY   = layoutData.dept_end_row ?? 10;

    // Assign department floor colors from dept_floor_0..3, ensuring adjacent zones differ
    const DEPT_FLOOR_COUNT = 4;
    let prevFloorIdx = -1;
    for (let zi = 0; zi < this.zones.length; zi++) {
      const zone = this.zones[zi];
      // Pick a floor index different from the previous zone
      let floorIdx = (zi * 3) % DEPT_FLOOR_COUNT;  // spread out by stride of 3
      if (floorIdx === prevFloorIdx) {
        floorIdx = (floorIdx + 1) % DEPT_FLOOR_COUNT;
      }
      prevFloorIdx = floorIdx;
      const floorKey = `dept_floor_${floorIdx}`;

      const startRow = deptStartGY + WALL_ROWS;
      const endRow   = this.rows - 1;

      for (let row = startRow; row <= endRow; row++) {
        for (let col = zone.start_col; col < zone.end_col; col++) {
          this._setFloor(col, row, floorKey);
        }
      }
    }
  }

  _buildDividers() {
    for (const divCol of this.dividerCols) {
      for (let row = WALL_ROWS; row < this.rows; row++) {
        const idx = row * this.cols + divCol;
        this._isDivider[idx] = true;
        // Alternate large (even rows) / small (odd rows) plant
        this._overlay[idx] = (row % 2 === 0) ? 'plant_large' : 'plant_small';
      }
    }
  }

  _buildEntities(employees, rooms, tools, layoutData) {
    this.employees    = [];
    this.executives   = [];
    this.ceo          = null;
    this.meetingRooms = [];
    this.tools        = [];

    for (const emp of employees) {
      const [gx, gy] = emp.desk_position || [0, 0];
      const entry = {
        ...emp,
        col: gx,
        row: gy + WALL_ROWS,  // canvas-row space
      };

      if (emp.id === CEO_ID) {
        this.ceo = entry;
      } else if (EXEC_IDS.has(emp.id)) {
        this.executives.push({ ...entry, role: EXEC_ROLES[emp.id] });
      } else {
        this.employees.push(entry);
      }
    }

    for (const room of rooms) {
      const [gx, gy] = room.position || [0, 0];
      this.meetingRooms.push({ ...room, col: gx, row: gy + WALL_ROWS });
    }

    for (const tool of tools) {
      if (tool.desk_position && tool.has_icon) {
        const [gx, gy] = tool.desk_position;
        this.tools.push({ ...tool, col: gx, row: gy + WALL_ROWS });
      }
    }
  }

  _setFloor(col, row, key) {
    if (col < 0 || col >= this.cols || row < 0 || row >= this.rows) return;
    this._floor[row * this.cols + col] = key;
  }

  getFloor(col, row)   { return this._floor[row * this.cols + col] || 'floor_stone_gray'; }
  getOverlay(col, row) { return this._overlay[row * this.cols + col]; }
  isDivider(col, row)  { return !!this._isDivider[row * this.cols + col]; }

  /** All employee-type entities (for Y-sort rendering). */
  allPeople() {
    return [
      ...(this.ceo ? [this.ceo] : []),
      ...this.executives,
      ...this.employees,
    ];
  }
}
