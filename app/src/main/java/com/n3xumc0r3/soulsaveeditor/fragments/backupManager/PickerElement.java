package com.n3xumc0r3.soulsaveeditor.fragments.backupManager;

import com.n3xumc0r3.soulsaveeditor.gamefiles.IGameFile;

public class PickerElement {
	private final IGameFile gameFile;

	private boolean isChecked = true;

	PickerElement(IGameFile gameFile) {
		this.gameFile = gameFile;
	}

	public IGameFile getGameFile() {
		return gameFile;
	}

	public void setChecked(boolean checked) {
		isChecked = checked;
	}

	public boolean isChecked() {
		return isChecked;
	}
}
