package com.n3xumc0r3.soulsaveeditor.gamefiles;

import android.util.Base64;

import java.security.InvalidAlgorithmParameterException;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;

import javax.crypto.BadPaddingException;
import javax.crypto.Cipher;
import javax.crypto.IllegalBlockSizeException;
import javax.crypto.NoSuchPaddingException;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

public class EncryptedJSONGameFile extends JSONGameFile {

	private final byte[] key;

	public EncryptedJSONGameFile(String name, String realName, String path, byte[] key) {
		super(name, realName, path);
		this.key = key;
	}

	@Override
	public String decrypt(byte[] contents) {
		byte[] cypherBytes = Base64.decode(contents, Base64.NO_WRAP);
		SecretKeySpec secretKeySpec = new SecretKeySpec(key, "DES");
		Cipher cipher = null;
		try {
			cipher = Cipher.getInstance("DES/CBC/PKCS5Padding");
		} catch (NoSuchAlgorithmException | NoSuchPaddingException e) {
			e.printStackTrace();
		}
		try {
			assert cipher != null;
			cipher.init(Cipher.DECRYPT_MODE, secretKeySpec, new IvParameterSpec(KeyProvider.getIv()));
		} catch (InvalidAlgorithmParameterException | InvalidKeyException e) {
			e.printStackTrace();
		}

		byte[] resultBytes = new byte[0];
		try {
			resultBytes = cipher.doFinal(cypherBytes);
		} catch (BadPaddingException | IllegalBlockSizeException e) {
			e.printStackTrace();
		}
		return super.decrypt(resultBytes);
	}

	@Override
	public byte[] encrypt(String contents) {
		byte[] bytes = super.encrypt(contents);
		SecretKeySpec secretKeySpec = new SecretKeySpec(key, "DES");
		Cipher cipher;
		try {
			cipher = Cipher.getInstance("DES/CBC/PKCS5Padding");
		} catch (NoSuchAlgorithmException | NoSuchPaddingException e) {
			e.printStackTrace();
			return new byte[0];
		}
		try {
			cipher.init(Cipher.ENCRYPT_MODE, secretKeySpec, new IvParameterSpec(KeyProvider.getIv()));
		} catch (InvalidAlgorithmParameterException | InvalidKeyException e) {
			e.printStackTrace();
		}

		byte[] resultBytes = new byte[0];
		try {
			resultBytes = cipher.doFinal(bytes);
		} catch (BadPaddingException | IllegalBlockSizeException e) {
			e.printStackTrace();
		}
		return Base64.encode(resultBytes, Base64.NO_WRAP);
	}
}
