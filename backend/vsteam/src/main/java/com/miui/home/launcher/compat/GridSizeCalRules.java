package com.miui.home.launcher.compat;

import static com.android.vsteam.Toolbox.getBooleanGlobal;
import static com.android.vsteam.Toolbox.getIntGlobal;
import static com.android.vsteam.Toolbox.vslog;

public class GridSizeCalRules {

    protected int mCellSize;
    protected int mMaxGridWidth;
    protected int mWorkspaceCellSideDefault;
    protected int mCellWidth;
    protected int mCellHeight;

    protected int mCellCountY;

    protected int mHotseatCellHeight;

    protected int mIndicatorHeight;
    protected int mHotseatMarginBottom;

    protected int WorkspaceIndicatorMarginBottom;


    public boolean customizeGridSize() {
        try {
            if (getBooleanGlobal("vs_os2_custom_layout")) {
                int customCellCountX = getIntGlobal("vs_layout_grid_x");
                int customCellCountY = getIntGlobal("vs_layout_grid_y");
                int cellWorkspaceHeight;
                boolean hideHotSeat = getBooleanGlobal("vs_layout_hide_hotseat");


                if (hideHotSeat) {
                    cellWorkspaceHeight = this.mCellSize * (this.mCellCountY + 2);
                    this.mCellHeight = (cellWorkspaceHeight - 180) / customCellCountY;
                    this.mHotseatCellHeight = 0;
                    this.mHotseatMarginBottom = 0;
                    this.mIndicatorHeight = 0;
                    this.WorkspaceIndicatorMarginBottom = 0;
                } else {
                    cellWorkspaceHeight = this.mCellSize * this.mCellCountY;
                    this.mCellHeight = cellWorkspaceHeight / customCellCountY;
                }

                this.mCellWidth = (this.mMaxGridWidth - mWorkspaceCellSideDefault) / customCellCountX;
                this.mCellSize = mCellWidth;


                vslog("sCellWidth: " + mCellWidth);
                vslog("mCellHeight: " + mCellHeight);
                vslog("sCellSize: " + mCellSize);
            }

        } catch (Exception e) {
            vslog("An unexpected exception occurred: " + e.getMessage());
        }

        return true;
    }


}
