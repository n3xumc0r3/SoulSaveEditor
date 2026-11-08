package com.n3xumc0r3.soulsaveeditor.gamefiles;

public interface IGameFile {
	String getName();

	String getRealName();

	String getPath();

	String decrypt(byte[] contents);

	byte[] encrypt(String contents);
}
