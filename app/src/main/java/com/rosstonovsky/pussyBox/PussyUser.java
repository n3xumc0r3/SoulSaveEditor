package com.rosstonovsky.pussyBox;

import android.annotation.SuppressLint;
import android.content.Context;
import android.os.Build;
import android.widget.Toast;

import com.rosstonovsky.catbox.CatUser;

import java.util.ArrayList;
import java.util.List;

public class PussyUser {

	private static String protectedCacheFolder;

	public static String getAppFilesFolder() {
		return CatUser.getAppFilesFolder();
	}

	public static String getDataFolder() {
		return CatUser.getDataFolder();
	}

	public static String getAppDataFolder() {
		return CatUser.getAppDataFolder();
	}

	public static long getId() {
		return CatUser.getId();
	}

	@SuppressLint("SdCardPath")
	public static void makeUser(Context context) {
		CatUser.makeUser(context);
		protectedCacheFolder = context.getCacheDir().getAbsolutePath();
	}

	public static String getProtectedCacheFolder() {
		return protectedCacheFolder;
	}
}
